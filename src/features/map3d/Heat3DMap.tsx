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

function updateGridLayer(map: Map, gridData: FeatureCollection | null): void {
  const data = gridData ?? EMPTY_GRID_DATA;
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
      type: 'fill',
      source: GRID_SOURCE_ID,
      paint: {
        'fill-color': '#3f8f57',
        'fill-opacity': 0.3
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
      {initializationError && (
        <div className="heat3dMapError" role="alert">
          3D 지도를 표시할 수 없습니다.
        </div>
      )}
    </div>
  );
}
