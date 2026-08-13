import { useEffect, useRef, useState } from 'react';
import type { FeatureCollection } from 'geojson';
import {
  Map,
  Marker,
  Popup,
  setWorkerUrl,
  type GeoJSONSource,
  type LngLatBoundsLike,
  type MapLayerMouseEvent,
  type StyleSpecification,
  type Subscription
} from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import type { GridResolution } from '../../types/dashboard';

setWorkerUrl(workerUrl);

const SEOUL_CENTER: [number, number] = [126.978, 37.5665];
const ALL_DISTRICTS = '전체';
const GRID_SOURCE_ID = 'gaon-100m-grid';
const GRID_FILL_LAYER_ID = 'gaon-100m-grid-fill';
const GRID_LINE_LAYER_ID = 'gaon-100m-grid-line';
const GRID_SELECTION_LAYER_ID = 'gaon-100m-grid-selection';
const LST_PROPERTY = 'mean_actual_lst';
const NUMERIC_LST_PROPERTY = '__gaon_numeric_lst';
const VISUAL_HEIGHT_PROPERTY = '__gaon_lst_visual_height';
// 2D 선택 외곽선과 같은 색을 사용하며, 실제 데이터 높이와 구분되는 얇은 선택 cap이다.
const GRID_SELECTION_COLOR = '#111827';
const GRID_SELECTION_CAP_HEIGHT = 12;
// 기존 2D 지표면온도 범례와 동일한 고정 구간 및 색상이다.
const LST_COLOR_LOW = '#6fbf73';
const LST_COLOR_MEDIUM = '#f2cf5b';
const LST_COLOR_HIGH = '#f29a4b';
const LST_COLOR_VERY_HIGH = '#cf3f3f';
const LST_COLOR_FALLBACK = '#d7dce3';
const LST_BREAK_LOW = 35;
const LST_BREAK_MEDIUM = 38;
const LST_BREAK_HIGH = 41;
// 현재 서울 전체 100m 데이터에서 검증된 공통 시각화 기준이다.
const SEOUL_LST_MIN = 24.4014;
const SEOUL_LST_MAX = 52.4678;
// MapLibre는 extrusion 높이를 meter 단위로 해석하지만, 아래 값은 실제 고도가 아닌 시각적 범위다.
const MIN_VISUAL_HEIGHT = 30;
const MAX_VISUAL_HEIGHT = 400;
const EMPTY_GRID_DATA: FeatureCollection = {
  type: 'FeatureCollection',
  features: []
};

const OSM_RASTER_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    'openstreetmap-raster': {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      maxzoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }
  },
  layers: [
    {
      id: 'openstreetmap-raster-layer',
      type: 'raster',
      source: 'openstreetmap-raster'
    }
  ]
};

interface Heat3DMapProps {
  gridData: FeatureCollection | null;
  resolution: GridResolution;
  selectedDistrict: string;
  selectedGridId: string | null;
  gridInfoSign: Map3DGridInfoSign | null;
  shelterSign: Map3DShelterSign | null;
  onGridFeatureClick: Map3DGridFeatureClickHandler;
  onClearSelectedGrid: Map3DClearSelectedGridHandler;
}

export interface Map3DGridInfoSign {
  gridId: string;
  position: { lng: number; lat: number };
  html: string;
}

export interface Map3DShelterSign {
  position: [number, number];
  name: string;
  addr: string;
  distance: number | null;
}

export type Map3DGridFeatureClickHandler = (
  feature: FeatureCollection['features'][number],
  position: { lng: number; lat: number }
) => void;

export type Map3DClearSelectedGridHandler = (gridId: string) => void;

interface MutableBounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

interface MapSignInstances {
  gridInfoPopup: Popup | null;
  gridInfoCloseSubscription: Subscription | null;
  shelterMarker: Marker | null;
  shelterPopup: Popup | null;
}

function extendBounds(input: unknown, bounds: MutableBounds): void {
  if (!Array.isArray(input)) return;

  if (
    input.length >= 2 &&
    typeof input[0] === 'number' &&
    typeof input[1] === 'number' &&
    Number.isFinite(input[0]) &&
    Number.isFinite(input[1])
  ) {
    const [longitude, latitude] = input;
    bounds.west = Math.min(bounds.west, longitude);
    bounds.south = Math.min(bounds.south, latitude);
    bounds.east = Math.max(bounds.east, longitude);
    bounds.north = Math.max(bounds.north, latitude);
    return;
  }

  input.forEach((item) => extendBounds(item, bounds));
}

function getGridBounds(gridData: FeatureCollection): LngLatBoundsLike | null {
  const bounds: MutableBounds = {
    west: Number.POSITIVE_INFINITY,
    south: Number.POSITIVE_INFINITY,
    east: Number.NEGATIVE_INFINITY,
    north: Number.NEGATIVE_INFINITY
  };

  gridData.features.forEach((feature) => {
    const geometry = feature.geometry;
    if (!geometry) return;

    if (geometry.type === 'GeometryCollection') {
      geometry.geometries.forEach((childGeometry) => {
        if ('coordinates' in childGeometry) {
          extendBounds(childGeometry.coordinates, bounds);
        }
      });
      return;
    }

    extendBounds(geometry.coordinates, bounds);
  });

  if (
    !Number.isFinite(bounds.west) ||
    !Number.isFinite(bounds.south) ||
    !Number.isFinite(bounds.east) ||
    !Number.isFinite(bounds.north)
  ) {
    return null;
  }

  return [
    [bounds.west, bounds.south],
    [bounds.east, bounds.north]
  ];
}

function getFiniteLst(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null;
  }

  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  return null;
}

function getGridId(properties: unknown): string | null {
  if (!properties || typeof properties !== 'object') return null;

  const record = properties as Record<string, unknown>;
  const value = record.grid_id ?? record.display_grid_id;
  if (typeof value === 'string' && value.trim() !== '') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return null;
}

function getOriginalGridFeature(
  gridData: FeatureCollection | null,
  renderedProperties: unknown
): FeatureCollection['features'][number] | null {
  const renderedGridId = getGridId(renderedProperties);
  if (!gridData || !renderedGridId) return null;

  return (
    gridData.features.find(
      (feature) => getGridId(feature.properties) === renderedGridId
    ) ?? null
  );
}

function clearMapSigns(instances: MapSignInstances): void {
  // React props 갱신으로 제거할 때는 사용자 X close callback을 실행하지 않는다.
  instances.gridInfoCloseSubscription?.unsubscribe();
  instances.gridInfoCloseSubscription = null;
  instances.gridInfoPopup?.remove();
  instances.shelterPopup?.remove();
  instances.shelterMarker?.remove();
  instances.gridInfoPopup = null;
  instances.shelterPopup = null;
  instances.shelterMarker = null;
}

function createShelterMarkerElement(shelter: Map3DShelterSign): HTMLDivElement {
  const element = document.createElement('div');
  element.className = 'map3dShelterMarker';
  element.setAttribute('aria-label', `무더위쉼터 ${shelter.name}`);

  const label = document.createElement('span');
  label.className = 'map3dShelterLabel';

  const category = document.createElement('b');
  category.textContent = '무더위쉼터';
  label.append(category);

  const name = document.createElement('span');
  name.textContent = shelter.name;
  label.append(name);

  if (shelter.distance !== null) {
    const distance = document.createElement('span');
    distance.className = 'map3dShelterDistance';
    distance.textContent = `여기서 약 ${Math.round(shelter.distance).toLocaleString()}m`;
    label.append(distance);
  }

  const pin = document.createElement('span');
  pin.className = 'map3dShelterPinDot';
  const icon = document.createElement('i');
  icon.textContent = '🏠';
  pin.append(icon);

  element.append(label, pin);
  return element;
}

function createShelterPopupContent(shelter: Map3DShelterSign): HTMLDivElement {
  const content = document.createElement('div');
  content.className = 'map3dShelterPopupContent';

  const name = document.createElement('strong');
  name.textContent = shelter.name;
  content.append(name);

  if (shelter.addr) {
    const address = document.createElement('span');
    address.textContent = shelter.addr;
    content.append(address);
  }

  if (shelter.distance !== null) {
    const distance = document.createElement('span');
    distance.textContent = `선택 격자에서 약 ${Math.round(shelter.distance)}m`;
    content.append(distance);
  }

  return content;
}

function updateMapSigns(
  map: Map,
  instances: MapSignInstances,
  gridInfoSign: Map3DGridInfoSign | null,
  shelterSign: Map3DShelterSign | null,
  onClearSelectedGrid: Map3DClearSelectedGridHandler
): void {
  clearMapSigns(instances);

  if (gridInfoSign) {
    const content = document.createElement('div');
    content.className = 'map3dGridInfoContent';
    // 2D Popup과 같은 buildGridTooltip 결과를 그대로 표시한다.
    content.innerHTML = gridInfoSign.html;
    const popup = new Popup({
      anchor: 'bottom',
      offset: 12,
      closeButton: true,
      closeOnClick: false,
      focusAfterOpen: false,
      maxWidth: 'none',
      className: 'map3dGridInfoPopup'
    })
      .setLngLat([gridInfoSign.position.lng, gridInfoSign.position.lat])
      .setDOMContent(content);
    let hasHandledClose = false;
    instances.gridInfoCloseSubscription = popup.on('close', () => {
      if (hasHandledClose) return;
      hasHandledClose = true;
      onClearSelectedGrid(gridInfoSign.gridId);
    });
    instances.gridInfoPopup = popup.addTo(map);
  }

  if (shelterSign) {
    const popup = new Popup({
      offset: 40,
      closeOnClick: false,
      focusAfterOpen: false,
      className: 'map3dShelterPopup'
    }).setDOMContent(createShelterPopupContent(shelterSign));
    const [latitude, longitude] = shelterSign.position;
    const marker = new Marker({
      element: createShelterMarkerElement(shelterSign),
      anchor: 'bottom'
    })
      .setLngLat([longitude, latitude])
      .setPopup(popup)
      .addTo(map);

    instances.shelterPopup = popup;
    instances.shelterMarker = marker;
  }
}

function getVisualHeight(lst: number): number {
  const normalized = (lst - SEOUL_LST_MIN) / (SEOUL_LST_MAX - SEOUL_LST_MIN);
  const clamped = Math.min(1, Math.max(0, normalized));
  return MIN_VISUAL_HEIGHT + clamped * (MAX_VISUAL_HEIGHT - MIN_VISUAL_HEIGHT);
}

function prepareExtrusionData(gridData: FeatureCollection): FeatureCollection {
  return {
    ...gridData,
    features: gridData.features.map((feature) => {
      const lst = getFiniteLst(feature.properties?.[LST_PROPERTY]);
      const visualHeight = lst === null ? 0 : getVisualHeight(lst);

      return {
        ...feature,
        properties: {
          ...(feature.properties ?? {}),
          [NUMERIC_LST_PROPERTY]: lst,
          [VISUAL_HEIGHT_PROPERTY]: visualHeight
        }
      };
    })
  };
}

type GridSelectionFilter = NonNullable<Parameters<Map['setFilter']>[1]>;

function getGridSelectionFilter(selectedGridId: string | null): GridSelectionFilter {
  if (selectedGridId === null) {
    return ['==', ['literal', true], ['literal', false]];
  }

  return [
    'any',
    ['==', ['to-string', ['get', 'grid_id']], selectedGridId],
    ['==', ['to-string', ['get', 'display_grid_id']], selectedGridId]
  ];
}

function updateGridSelection(map: Map, selectedGridId: string | null): void {
  if (!map.getLayer(GRID_SELECTION_LAYER_ID)) return;
  map.setFilter(GRID_SELECTION_LAYER_ID, getGridSelectionFilter(selectedGridId));
}

function updateGridLayer(map: Map, gridData: FeatureCollection | null): void {
  const data = gridData ? prepareExtrusionData(gridData) : EMPTY_GRID_DATA;
  const existingSource = map.getSource(GRID_SOURCE_ID);

  if (existingSource) {
    void (existingSource as GeoJSONSource).setData(data);
  } else {
    map.addSource(GRID_SOURCE_ID, {
      type: 'geojson',
      data
    });
  }

  if (!map.getLayer(GRID_FILL_LAYER_ID)) {
    map.addLayer({
      id: GRID_FILL_LAYER_ID,
      type: 'fill-extrusion',
      source: GRID_SOURCE_ID,
      paint: {
        'fill-extrusion-base': 0,
        'fill-extrusion-color': [
          'case',
          ['==', ['typeof', ['get', NUMERIC_LST_PROPERTY]], 'number'],
          [
            'step',
            ['get', NUMERIC_LST_PROPERTY],
            LST_COLOR_LOW,
            LST_BREAK_LOW,
            LST_COLOR_MEDIUM,
            LST_BREAK_MEDIUM,
            LST_COLOR_HIGH,
            LST_BREAK_HIGH,
            LST_COLOR_VERY_HIGH
          ],
          LST_COLOR_FALLBACK
        ],
        'fill-extrusion-height': [
          'number',
          ['get', VISUAL_HEIGHT_PROPERTY],
          0
        ],
        'fill-extrusion-opacity': 0.72
      }
    });
  }

  if (!map.getLayer(GRID_LINE_LAYER_ID)) {
    map.addLayer({
      id: GRID_LINE_LAYER_ID,
      type: 'line',
      source: GRID_SOURCE_ID,
      paint: {
        'line-color': '#174d2b',
        'line-opacity': 0.8,
        'line-width': 0.65
      }
    });
  }

  if (!map.getLayer(GRID_SELECTION_LAYER_ID)) {
    map.addLayer({
      id: GRID_SELECTION_LAYER_ID,
      type: 'fill-extrusion',
      source: GRID_SOURCE_ID,
      filter: getGridSelectionFilter(null),
      paint: {
        'fill-extrusion-base': [
          'number',
          ['get', VISUAL_HEIGHT_PROPERTY],
          0
        ],
        'fill-extrusion-height': [
          '+',
          ['number', ['get', VISUAL_HEIGHT_PROPERTY], 0],
          GRID_SELECTION_CAP_HEIGHT
        ],
        'fill-extrusion-color': GRID_SELECTION_COLOR,
        'fill-extrusion-opacity': 1
      }
    });
  }

  if (!gridData || gridData.features.length === 0) return;

  const bounds = getGridBounds(gridData);
  if (!bounds) return;

  map.fitBounds(bounds, {
    padding: 80,
    maxZoom: 14,
    bearing: -15,
    pitch: 55,
    duration: 0
  });
}

export default function Heat3DMap({
  gridData,
  resolution,
  selectedDistrict,
  selectedGridId,
  gridInfoSign,
  shelterSign,
  onGridFeatureClick,
  onClearSelectedGrid
}: Heat3DMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Map | null>(null);
  const gridDataRef = useRef(gridData);
  const selectedGridIdRef = useRef(selectedGridId);
  const gridInfoSignRef = useRef(gridInfoSign);
  const shelterSignRef = useRef(shelterSign);
  const onGridFeatureClickRef = useRef(onGridFeatureClick);
  const onClearSelectedGridRef = useRef(onClearSelectedGrid);
  const mapSignsRef = useRef<MapSignInstances>({
    gridInfoPopup: null,
    gridInfoCloseSubscription: null,
    shelterMarker: null,
    shelterPopup: null
  });
  const isMapLoadedRef = useRef(false);
  const [initializationError, setInitializationError] = useState(false);
  gridDataRef.current = gridData;
  selectedGridIdRef.current = selectedGridId;
  onGridFeatureClickRef.current = onGridFeatureClick;
  onClearSelectedGridRef.current = onClearSelectedGrid;

  const hasSupportedGridData =
    resolution === '100m' &&
    selectedDistrict !== ALL_DISTRICTS &&
    Boolean(gridData?.features.length);
  gridInfoSignRef.current = hasSupportedGridData ? gridInfoSign : null;
  shelterSignRef.current = hasSupportedGridData ? shelterSign : null;

  useEffect(() => {
    const container = containerRef.current;
    if (!container || mapRef.current) return;

    let map: Map;
    let handleLoad: (() => void) | null = null;
    let gridClickSubscription: Subscription | null = null;

    const handleGridClick = (event: MapLayerMouseEvent) => {
      const renderedFeature = event.features?.[0];
      if (!renderedFeature) return;

      // MapLibre 전용 __gaon_* 속성이 상세 상태로 넘어가지 않도록 원본 feature를 되찾는다.
      const originalFeature = getOriginalGridFeature(
        gridDataRef.current,
        renderedFeature.properties
      );
      if (!originalFeature) return;

      onGridFeatureClickRef.current(originalFeature, {
        lng: event.lngLat.lng,
        lat: event.lngLat.lat
      });
    };

    try {
      map = new Map({
        container,
        style: OSM_RASTER_STYLE,
        center: SEOUL_CENTER,
        zoom: 11,
        pitch: 55,
        bearing: -15,
        attributionControl: { compact: false },
        canvasContextAttributes: { antialias: true }
      });
      mapRef.current = map;

      handleLoad = () => {
        isMapLoadedRef.current = true;
        updateGridLayer(map, gridDataRef.current);
        updateGridSelection(map, selectedGridIdRef.current);
        gridClickSubscription = map.on('click', GRID_FILL_LAYER_ID, handleGridClick);
        updateMapSigns(
          map,
          mapSignsRef.current,
          gridInfoSignRef.current,
          shelterSignRef.current,
          (gridId) => onClearSelectedGridRef.current(gridId)
        );
      };
      map.once('load', handleLoad);
    } catch {
      setInitializationError(true);
      return;
    }

    return () => {
      if (handleLoad) map.off('load', handleLoad);
      gridClickSubscription?.unsubscribe();
      clearMapSigns(mapSignsRef.current);
      isMapLoadedRef.current = false;
      map.remove();
      if (mapRef.current === map) {
        mapRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isMapLoadedRef.current) return;

    updateGridLayer(map, gridData);
  }, [gridData]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isMapLoadedRef.current) return;

    updateGridSelection(map, selectedGridId);
  }, [selectedGridId]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isMapLoadedRef.current) return;

    updateMapSigns(
      map,
      mapSignsRef.current,
      hasSupportedGridData ? gridInfoSign : null,
      hasSupportedGridData ? shelterSign : null,
      (gridId) => onClearSelectedGridRef.current(gridId)
    );
  }, [gridInfoSign, hasSupportedGridData, shelterSign]);

  return (
    <div className="heat3dMap" role="region" aria-label="서울 3D 지도">
      <div ref={containerRef} className="heat3dMapCanvas" />
      {!hasSupportedGridData && !initializationError && (
        <div className="heat3dMapNotice" role="status">
          {resolution === '100m' && selectedDistrict !== ALL_DISTRICTS
            ? '100m 격자 데이터를 준비 중입니다.'
            : '100m 3D 상세보기는 자치구 선택 후 이용할 수 있습니다.'}
        </div>
      )}
      {hasSupportedGridData && !initializationError && (
        <div className="heat3dMapNotice heat3dHeightNotice" role="note">
          격자 높이는 서울 전체 100m 격자의 지표면온도 범위를 기준으로 표현한 상대적
          시각화이며 실제 지형·건물 높이가 아닙니다.
        </div>
      )}
      {initializationError && (
        <div className="heat3dMapError" role="alert">
          3D 지도를 표시할 수 없습니다.
        </div>
      )}
    </div>
  );
}
