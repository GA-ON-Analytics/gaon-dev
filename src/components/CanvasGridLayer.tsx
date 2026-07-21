import { useEffect, useMemo, useRef } from 'react';
import type { Feature, FeatureCollection, Geometry, Polygon, MultiPolygon } from 'geojson';
import L, { type LatLng, type LeafletMouseEvent, type PathOptions } from 'leaflet';
import { useMap } from 'react-leaflet';

type Ring = Array<[number, number]>;
type PolygonRings = Ring[];

interface IndexedFeature {
  feature: Feature<Geometry>;
  polygons: PolygonRings[];
  west: number;
  south: number;
  east: number;
  north: number;
}

interface CanvasGridLayerProps {
  data: FeatureCollection;
  activeTool: string;
  selectedGridId: string | null;
  compareGridId?: string | null;
  style: (feature: Feature<Geometry>) => PathOptions;
  tooltip: (feature: Feature<Geometry>) => string;
  onFeatureClick: (feature: Feature<Geometry>, latLng: LatLng) => void;
}

const INDEX_CELL_DEGREES = 0.01;

function getGridId(feature: Feature<Geometry>) {
  const properties = feature.properties ?? {};
  const value = properties.grid_id ?? properties.display_grid_id;
  return typeof value === 'string' ? value : '';
}

function geometryToPolygons(geometry: Geometry): PolygonRings[] {
  if (geometry.type === 'Polygon') {
    return [(geometry as Polygon).coordinates as PolygonRings];
  }

  if (geometry.type === 'MultiPolygon') {
    return (geometry as MultiPolygon).coordinates as PolygonRings[];
  }

  return [];
}

function pointInRing(lng: number, lat: number, ring: Ring) {
  let inside = false;

  for (let current = 0, previous = ring.length - 1; current < ring.length; previous = current++) {
    const [currentLng, currentLat] = ring[current];
    const [previousLng, previousLat] = ring[previous];
    const crosses =
      currentLat > lat !== previousLat > lat &&
      lng <
        ((previousLng - currentLng) * (lat - currentLat)) /
          (previousLat - currentLat || Number.EPSILON) +
          currentLng;

    if (crosses) inside = !inside;
  }

  return inside;
}

function containsPoint(indexed: IndexedFeature, lng: number, lat: number) {
  if (lng < indexed.west || lng > indexed.east || lat < indexed.south || lat > indexed.north) {
    return false;
  }

  return indexed.polygons.some((rings) => {
    if (rings.length === 0 || !pointInRing(lng, lat, rings[0])) return false;
    return !rings.slice(1).some((hole) => pointInRing(lng, lat, hole));
  });
}

class FeatureSpatialIndex {
  readonly features: IndexedFeature[];
  private readonly buckets = new Map<string, number[]>();

  constructor(collection: FeatureCollection) {
    this.features = collection.features.flatMap((rawFeature) => {
      const feature = rawFeature as Feature<Geometry>;
      const polygons = geometryToPolygons(feature.geometry);
      if (polygons.length === 0) return [];

      let west = Infinity;
      let south = Infinity;
      let east = -Infinity;
      let north = -Infinity;

      for (const rings of polygons) {
        for (const ring of rings) {
          for (const [lng, lat] of ring) {
            west = Math.min(west, lng);
            south = Math.min(south, lat);
            east = Math.max(east, lng);
            north = Math.max(north, lat);
          }
        }
      }

      return [{ feature, polygons, west, south, east, north }];
    });

    this.features.forEach((feature, featureIndex) => {
      const minX = Math.floor(feature.west / INDEX_CELL_DEGREES);
      const maxX = Math.floor(feature.east / INDEX_CELL_DEGREES);
      const minY = Math.floor(feature.south / INDEX_CELL_DEGREES);
      const maxY = Math.floor(feature.north / INDEX_CELL_DEGREES);

      for (let x = minX; x <= maxX; x += 1) {
        for (let y = minY; y <= maxY; y += 1) {
          const key = `${x}:${y}`;
          const bucket = this.buckets.get(key);
          if (bucket) bucket.push(featureIndex);
          else this.buckets.set(key, [featureIndex]);
        }
      }
    });
  }

  queryBounds(west: number, south: number, east: number, north: number) {
    const indexes = new Set<number>();
    const minX = Math.floor(west / INDEX_CELL_DEGREES);
    const maxX = Math.floor(east / INDEX_CELL_DEGREES);
    const minY = Math.floor(south / INDEX_CELL_DEGREES);
    const maxY = Math.floor(north / INDEX_CELL_DEGREES);

    for (let x = minX; x <= maxX; x += 1) {
      for (let y = minY; y <= maxY; y += 1) {
        for (const index of this.buckets.get(`${x}:${y}`) ?? []) indexes.add(index);
      }
    }

    return [...indexes]
      .map((index) => this.features[index])
      .filter(
        (feature) =>
          feature.west <= east &&
          feature.east >= west &&
          feature.south <= north &&
          feature.north >= south
      );
  }

  queryPoint(lng: number, lat: number) {
    const x = Math.floor(lng / INDEX_CELL_DEGREES);
    const y = Math.floor(lat / INDEX_CELL_DEGREES);

    for (const index of this.buckets.get(`${x}:${y}`) ?? []) {
      const candidate = this.features[index];
      if (containsPoint(candidate, lng, lat)) return candidate.feature;
    }

    return null;
  }
}

class GeoJsonCanvasTiles extends L.GridLayer {
  private renderStyle: (feature: Feature<Geometry>) => PathOptions;
  private selectedGridId: string | null;
  private compareGridId: string | null;

  constructor(
    private readonly index: FeatureSpatialIndex,
    renderStyle: (feature: Feature<Geometry>) => PathOptions,
    selectedGridId: string | null,
    compareGridId: string | null
  ) {
    super({ pane: 'gridCanvasPane', tileSize: 256, updateWhenIdle: true, keepBuffer: 2 });
    this.renderStyle = renderStyle;
    this.selectedGridId = selectedGridId;
    this.compareGridId = compareGridId;
  }

  updatePresentation(
    renderStyle: (feature: Feature<Geometry>) => PathOptions,
    selectedGridId: string | null,
    compareGridId: string | null
  ) {
    this.renderStyle = renderStyle;
    this.selectedGridId = selectedGridId;
    this.compareGridId = compareGridId;
    this.redraw();
  }

  createTile(coords: L.Coords) {
    const canvas = document.createElement('canvas');
    const tileSize = this.getTileSize();
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = tileSize.x * pixelRatio;
    canvas.height = tileSize.y * pixelRatio;
    canvas.style.width = `${tileSize.x}px`;
    canvas.style.height = `${tileSize.y}px`;
    canvas.style.pointerEvents = 'none';

    const context = canvas.getContext('2d');
    const map = (this as unknown as { _map: L.Map })._map;
    if (!context || !map) return canvas;
    context.scale(pixelRatio, pixelRatio);

    const tileOrigin = coords.scaleBy(tileSize);
    const northWest = map.unproject(tileOrigin, coords.z);
    const southEast = map.unproject(tileOrigin.add(tileSize), coords.z);
    const candidates = this.index.queryBounds(
      northWest.lng,
      southEast.lat,
      southEast.lng,
      northWest.lat
    );

    for (const indexed of candidates) {
      const feature = indexed.feature;
      const gid = getGridId(feature);
      const isSelected = gid === this.selectedGridId;
      const isCompare = this.compareGridId != null && gid === this.compareGridId;
      const baseStyle = this.renderStyle(feature);
      const style: PathOptions = isSelected
        ? { ...baseStyle, color: '#111827', weight: 3, opacity: 1 }
        : isCompare
        ? { ...baseStyle, color: '#1c7ed6', weight: 3, opacity: 1 }   // 비교 격자 = 파랑
        : baseStyle;

      context.beginPath();
      for (const rings of indexed.polygons) {
        for (const ring of rings) {
          ring.forEach(([lng, lat], pointIndex) => {
            const point = map.project([lat, lng], coords.z).subtract(tileOrigin);
            if (pointIndex === 0) context.moveTo(point.x, point.y);
            else context.lineTo(point.x, point.y);
          });
          context.closePath();
        }
      }

      context.globalAlpha = Number(style.fillOpacity ?? 0.2);
      context.fillStyle = String(style.fillColor ?? style.color ?? '#d7dce3');
      context.fill('evenodd');

      const weight = Number(style.weight ?? 0);
      if (weight > 0) {
        context.globalAlpha = Number(style.opacity ?? 1);
        context.strokeStyle = String(style.color ?? '#3388ff');
        context.lineWidth = weight;
        context.stroke();
      }
    }

    context.globalAlpha = 1;
    return canvas;
  }
}

export default function CanvasGridLayer({
  data,
  activeTool,
  selectedGridId,
  compareGridId = null,
  style,
  tooltip,
  onFeatureClick
}: CanvasGridLayerProps) {
  const map = useMap();
  const index = useMemo(() => new FeatureSpatialIndex(data), [data]);
  const layerRef = useRef<GeoJsonCanvasTiles | null>(null);

  useEffect(() => {
    let pane = map.getPane('gridCanvasPane');
    if (!pane) pane = map.createPane('gridCanvasPane');
    pane.style.zIndex = '350';
    pane.style.pointerEvents = 'none';

    const layer = new GeoJsonCanvasTiles(index, style, selectedGridId, compareGridId);
    layerRef.current = layer;
    layer.addTo(map);

    return () => {
      layerRef.current = null;
      layer.removeFrom(map);
    };
  }, [index, map]);

  useEffect(() => {
    layerRef.current?.updatePresentation(style, selectedGridId, compareGridId);
  }, [selectedGridId, compareGridId, style]);

  useEffect(() => {
    const handleClick = (event: LeafletMouseEvent) => {
      if (activeTool !== '지도선택') return;
      const feature = index.queryPoint(event.latlng.lng, event.latlng.lat);
      if (feature) onFeatureClick(feature, event.latlng);
    };

    let hoverTooltip: L.Tooltip | null = null;
    let hoverRect: L.Rectangle | null = null;   // 호버 중인 격자 주황 테두리
    let hoveredGridId = '';
    let frame = 0;
    const handleMouseMove = (event: LeafletMouseEvent) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const feature = index.queryPoint(event.latlng.lng, event.latlng.lat);
        const gridId = feature ? getGridId(feature) : '';

        if (!feature) {
          if (hoverTooltip) map.removeLayer(hoverTooltip);
          if (hoverRect) map.removeLayer(hoverRect);
          hoverTooltip = null;
          hoverRect = null;
          hoveredGridId = '';
          return;
        }

        if (!hoverTooltip || hoveredGridId !== gridId) {
          if (hoverTooltip) map.removeLayer(hoverTooltip);
          hoverTooltip = L.tooltip({
            className: 'gridTempTooltip',
            direction: 'top',
            opacity: 1
          })
            .setContent(tooltip(feature))
            .setLatLng(event.latlng);
          hoverTooltip.addTo(map);

          // 주황 테두리: '지도선택' 모드에서만, 호버한 격자 경계 위에 그린다
          if (activeTool === '지도선택') {
            const bounds = L.geoJSON(feature).getBounds();
            if (hoverRect) hoverRect.setBounds(bounds);
            else
              hoverRect = L.rectangle(bounds, {
                color: '#e8590c',
                weight: 2,
                fill: false,
                interactive: false
              }).addTo(map);
          }
          hoveredGridId = gridId;
        }

        hoverTooltip.setLatLng(event.latlng);
      });
    };
    const clearTooltip = () => {
      cancelAnimationFrame(frame);
      if (hoverTooltip) map.removeLayer(hoverTooltip);
      if (hoverRect) map.removeLayer(hoverRect);
      hoverTooltip = null;
      hoverRect = null;
      hoveredGridId = '';
    };

    map.on('click', handleClick);
    map.on('mousemove', handleMouseMove);
    map.on('mouseout', clearTooltip);

    return () => {
      cancelAnimationFrame(frame);
      map.off('click', handleClick);
      map.off('mousemove', handleMouseMove);
      map.off('mouseout', clearTooltip);
      if (hoverTooltip) map.removeLayer(hoverTooltip);
      if (hoverRect) map.removeLayer(hoverRect);
    };
  }, [activeTool, index, map, onFeatureClick, tooltip]);

  return null;
}
