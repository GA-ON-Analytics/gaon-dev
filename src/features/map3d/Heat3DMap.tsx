import { useEffect, useRef, useState } from 'react';
import type { FeatureCollection } from 'geojson';
import {
  Map,
  setWorkerUrl,
  type GeoJSONSource,
  type LngLatBoundsLike,
  type StyleSpecification
} from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import type { GridResolution } from '../../types/dashboard';

setWorkerUrl(workerUrl);

const SEOUL_CENTER: [number, number] = [126.978, 37.5665];
const ALL_DISTRICTS = '전체';
const GRID_SOURCE_ID = 'gaon-100m-grid';
const GRID_FILL_LAYER_ID = 'gaon-100m-grid-fill';
const GRID_LINE_LAYER_ID = 'gaon-100m-grid-line';
const LST_PROPERTY = 'mean_actual_lst';
const VISUAL_HEIGHT_PROPERTY = '__gaon_lst_visual_height';
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
}

interface MutableBounds {
  west: number;
  south: number;
  east: number;
  north: number;
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
          [VISUAL_HEIGHT_PROPERTY]: visualHeight
        }
      };
    })
  };
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
        'fill-extrusion-color': '#3f8f57',
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
  selectedDistrict
}: Heat3DMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Map | null>(null);
  const gridDataRef = useRef(gridData);
  const isMapLoadedRef = useRef(false);
  const [initializationError, setInitializationError] = useState(false);
  gridDataRef.current = gridData;

  const hasSupportedGridData =
    resolution === '100m' &&
    selectedDistrict !== ALL_DISTRICTS &&
    Boolean(gridData?.features.length);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || mapRef.current) return;

    let map: Map;
    let handleLoad: (() => void) | null = null;

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
      };
      map.once('load', handleLoad);
    } catch {
      setInitializationError(true);
      return;
    }

    return () => {
      if (handleLoad) map.off('load', handleLoad);
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
