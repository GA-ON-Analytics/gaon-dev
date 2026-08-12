import { useEffect, useRef, useState } from 'react';
import { Map, setWorkerUrl, type StyleSpecification } from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';

setWorkerUrl(workerUrl);

const SEOUL_CENTER: [number, number] = [126.978, 37.5665];

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

export default function Heat3DMap() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Map | null>(null);
  const [initializationError, setInitializationError] = useState(false);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || mapRef.current) return;

    let map: Map;

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
    } catch {
      setInitializationError(true);
      return;
    }

    return () => {
      map.remove();
      if (mapRef.current === map) {
        mapRef.current = null;
      }
    };
  }, []);

  return (
    <div className="heat3dMap" role="region" aria-label="서울 3D 지도">
      <div ref={containerRef} className="heat3dMapCanvas" />
      {initializationError && (
        <div className="heat3dMapError" role="alert">
          3D 지도를 표시할 수 없습니다.
        </div>
      )}
    </div>
  );
}
