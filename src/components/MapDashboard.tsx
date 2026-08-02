import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FocusEvent as ReactFocusEvent,
  type MouseEvent as ReactMouseEvent
} from 'react';
import type { Feature, FeatureCollection, Geometry } from 'geojson';
import L, { type PathOptions } from 'leaflet';
import {
  GeoJSON,
  MapContainer,
  Marker,
  Popup,
  ScaleControl,
  TileLayer,
  Tooltip,
  useMap,
  useMapEvents,
  ZoomControl
} from 'react-leaflet';
import {
  getDistrictResolutionGrid,
  getGeoJson,
  getResolutionGrid
} from '../services/api';
import type {
  GridAnalysisProperties,
  GridResolution,
  LayerKey
} from '../types/dashboard';
import GridDetailSidePanel from './GridDetailSidePanel';
import AiChatLauncher from './AiChatLauncher';
import MapLegend from './MapLegend';
import CanvasGridLayer from './CanvasGridLayer';

const ALL_DISTRICTS = '전체';
const DEFAULT_DISTRICT = ALL_DISTRICTS;
const DEFAULT_LAYER: LayerKey = 'priority_score';
const DEFAULT_GRID_RESOLUTION: GridResolution = '100m';
const DEFAULT_CENTER: [number, number] = [37.5665, 126.978];
const SEOUL_OVERVIEW_ZOOM = 11;
const DISTRICT_DETAIL_ZOOM = 13;
const DISTRICT_OVERVIEW_ZOOM = 12;
// 라벨 칸이 이미 '분석 기준'이므로 값에는 기간만 (중복 접두어 제거 → 한 줄)
const ANALYSIS_PERIOD_LABEL = '2023~2025년 여름철 평균';
const DATA_PENDING = '데이터 준비중';
const NEUTRAL_COLOR = '#d7dce3';

interface DistrictOption {
  district: string;
  sigCode: string;
  center: [number, number];
  labelCenter: [number, number];
}

interface LayerOption {
  key: LayerKey;
  label: string;
  help: string;   // 버튼 안에 보이는 짧은 설명
  desc: string;   // 마우스 올리면 뜨는 상세 툴팁
}

interface GridResolutionOption {
  key: GridResolution;
  label: string;
}

const HEAT_ISLAND_INDICATORS: LayerOption[] = [
  {
    key: 'priority_score',
    label: '개선 우선순위',
    help: '종합 점수 0~100',
    desc: '녹지·불투수면·온도 등을 종합해 개선이 시급한 정도를 0~100으로 매긴 점수예요. 높을수록 우선 대상.'
  },
  {
    key: 'mean_actual_anomaly',
    label: '현재 열 위험',
    help: '구 평균 대비 온도차',
    desc: '같은 자치구 평균보다 이 격자가 얼마나 더 뜨거운지(℃)를 보여줘요. 양수일수록 더 더운 지역.'
  },
  {
    key: 'mean_actual_lst',
    label: '실제 지표면온도',
    help: '위성 관측 지표온도',
    desc: '위성으로 관측한 지표면 온도(℃)예요. 2023~2025년 여름철 평균 기준.'
  },
  // '시나리오 저감효과'(green_delta_c) 레이어는 뺐다. 단일 고정 시나리오(녹지+5%p·NDVI+0.03·
  // 불투수-5%p) 하나만 지도 전체에 칠하는 방식이라, 우측 패널의 시뮬레이션 값과 어긋나 혼란을
  // 줬다. 시나리오별 저감효과는 우측 상세 패널에서 시나리오를 골라 보는 방식으로 옮긴다.
  // green_delta_c 필드 자체는 priority_score 계산에 쓰이므로 데이터에는 그대로 남아 있다.
  {
    key: 'green_ratio',
    label: '녹지율',
    help: '식생이 덮은 비율',
    desc: '격자 면적 중 식생(풀·나무 등)이 덮은 비율이에요. 높을수록 시원한 편.'
  },
  {
    key: 'ndvi',
    label: '식생지수(NDVI)',
    help: '식생 활력 지수',
    desc: '위성 기반 식생 활력도(−1~1)예요. 값이 클수록 초록이 무성합니다.'
  },
  {
    key: 'building_ratio',
    label: '건물 비율',
    help: '건물이 덮은 비율',
    desc: '격자 면적 중 건물이 차지하는 비율이에요. 높을수록 열이 쌓이기 쉬움.'
  },
  {
    key: 'impervious_ratio',
    label: '불투수면 비율',
    help: '아스팔트·콘크리트 비율',
    desc: '물이 스며들지 못하는 포장면(아스팔트·콘크리트) 비율이에요. 높을수록 더 뜨거움.'
  },
  {
    key: 'nearest_shelter_distance_m',
    label: '쉼터 접근성',
    help: '가장 가까운 쉼터 거리',
    desc: '가장 가까운 무더위쉼터까지의 거리(m)예요. 가까울수록 폭염 대응에 유리합니다.'
  }
];

const GRID_RESOLUTION_OPTIONS: GridResolutionOption[] = [
  { key: '100m', label: '100M' },
  { key: '250m', label: '250M' },
  { key: '500m', label: '500M' }
];

const DISTRICT_LABEL_CENTER_OVERRIDES: Record<string, [number, number]> = {
  종로구: [37.576, 126.982]
};

function getFeatureProperties(feature?: Feature<Geometry>): GridAnalysisProperties {
  return (feature?.properties ?? {}) as GridAnalysisProperties;
}

function getDistrictName(feature: Feature<Geometry>) {
  const properties = getFeatureProperties(feature);
  if (typeof properties.district === 'string') return properties.district;
  if (typeof properties.gu_name === 'string') return properties.gu_name;
  return '';
}

function getDistrictCode(feature: Feature<Geometry>) {
  const properties = getFeatureProperties(feature);
  if (typeof properties.sig_cd === 'string') return properties.sig_cd;
  if (typeof properties.gu_code === 'string' || typeof properties.gu_code === 'number') {
    return String(properties.gu_code);
  }
  return '';
}

function getResolutionMeters(resolution: GridResolution) {
  return Number(resolution.replace('m', ''));
}

function getFeatureGuCode(feature: Feature<Geometry>) {
  const properties = getFeatureProperties(feature);
  const guCode = properties.gu_code ?? properties.sig_cd;
  return typeof guCode === 'string' || typeof guCode === 'number' ? String(guCode) : '';
}

function filterGridByDistrict(geoJson: FeatureCollection, sigCode: string) {
  return {
    ...geoJson,
    features: geoJson.features.filter((feature) =>
      getFeatureGuCode(feature as Feature<Geometry>) === sigCode
    )
  };
}

function collectLngLatPairs(input: unknown, pairs: Array<[number, number]> = []) {
  if (!Array.isArray(input)) {
    return pairs;
  }

  if (
    input.length >= 2 &&
    typeof input[0] === 'number' &&
    typeof input[1] === 'number' &&
    Number.isFinite(input[0]) &&
    Number.isFinite(input[1])
  ) {
    pairs.push([input[0], input[1]]);
    return pairs;
  }

  input.forEach((item) => collectLngLatPairs(item, pairs));
  return pairs;
}

function getFeatureCenter(feature: Feature<Geometry>): [number, number] {
  const pairs =
    feature.geometry.type === 'GeometryCollection'
      ? feature.geometry.geometries.flatMap((geometry) =>
          'coordinates' in geometry ? collectLngLatPairs(geometry.coordinates) : []
        )
      : collectLngLatPairs(feature.geometry.coordinates);

  if (pairs.length === 0) {
    return DEFAULT_CENTER;
  }

  const totals = pairs.reduce(
    (result, [lng, lat]) => ({
      lng: result.lng + lng,
      lat: result.lat + lat
    }),
    { lng: 0, lat: 0 }
  );

  return [totals.lat / pairs.length, totals.lng / pairs.length];
}

function buildDistrictOptions(geoJson: FeatureCollection | null): DistrictOption[] {
  return (geoJson?.features ?? [])
    .map((feature) => {
      const typedFeature = feature as Feature<Geometry>;
      const district = getDistrictName(typedFeature);
      const sigCode = getDistrictCode(typedFeature);
      const center = getFeatureCenter(typedFeature);

      return {
        district,
        sigCode,
        center,
        labelCenter: DISTRICT_LABEL_CENTER_OVERRIDES[district] ?? center
      };
    })
    .filter((option) => option.district && option.sigCode);
}

function getLayerLabel(layer: LayerKey) {
  return HEAT_ISLAND_INDICATORS.find((indicator) => indicator.key === layer)?.label ?? layer;
}

function getNumericProperty(properties: GridAnalysisProperties, key: LayerKey) {
  const value: unknown = properties[key];   // geojson 값은 문자열일 수도 있어 unknown으로 받는다

  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  return null;
}

function getGridIdentifier(properties: GridAnalysisProperties | null) {
  if (!properties) {
    return DATA_PENDING;
  }

  return properties.grid_id ?? properties.display_grid_id ?? DATA_PENDING;
}

function formatNumber(value: number, digits = 1) {
  return Number.isFinite(value) ? value.toFixed(digits) : DATA_PENDING;
}

function formatAnyProperty(properties: GridAnalysisProperties | null, key: keyof GridAnalysisProperties) {
  if (!properties) {
    return DATA_PENDING;
  }

  const value = properties[key];

  if (value === undefined || value === null || value === '') {
    return DATA_PENDING;
  }

  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return DATA_PENDING;
    if (
      key === 'green_ratio' ||
      key === 'building_ratio' ||
      key === 'impervious_ratio' ||
      key === 'road_ratio' ||
      key === 'zoning_residential_ratio' ||
      key === 'zoning_commercial_ratio' ||
      key === 'zoning_industrial_ratio' ||
      key === 'zoning_green_ratio' ||
      key === 'dong_elderly_ratio'
    ) {
      return `${formatNumber(value * 100, 1)}%`;
    }
    if (key === 'ndvi' || key === 'albedo') return formatNumber(value, 2);
    if (key === 'slope_deg') return `${formatNumber(value, 1)}°`;
    if (
      key === 'mean_actual_lst' ||
      key === 'mean_actual_anomaly' ||
      key === 'seoul_anomaly' ||
      key === 'pred_anomaly' ||
      key === 'pred_anomaly_std'
    ) {
      return `${formatNumber(value, 1)}℃`;
    }
    if (
      key === 'nearest_park_distance_m' ||
      key === 'nearest_stream_distance_m' ||
      key === 'nearest_shelter_distance_m' ||
      key === 'elevation_m'
    ) {
      return `${formatNumber(value, 0)}m`;
    }
    if (key === 'park_area_within_500m' || key === 'area_m2') {
      return `${formatNumber(value, 0)}㎡`;
    }
    if (key === 'top1_shap' || key === 'top2_shap' || key === 'top3_shap') {
      return formatNumber(value, 3);
    }

    return Number.isInteger(value) ? value.toLocaleString() : formatNumber(value, 1);
  }

  return String(value);
}

function formatLayerProperty(properties: GridAnalysisProperties | null, layer: LayerKey) {
  return formatAnyProperty(properties, layer);
}

function colorByScore(value: number, highIsRisk = true) {
  if (highIsRisk) {
    if (value >= 80) return '#cf3f3f';
    if (value >= 60) return '#f29a4b';
    if (value >= 40) return '#f2cf5b';
    return '#6fbf73';
  }

  if (value >= 80) return '#2f855a';
  if (value >= 60) return '#78b66a';
  if (value >= 40) return '#f2cf5b';
  return '#cf3f3f';
}

function colorByRatio(value: number, highIsRisk: boolean) {
  if (highIsRisk) {
    if (value >= 0.7) return '#cf3f3f';
    if (value >= 0.5) return '#f29a4b';
    if (value >= 0.3) return '#f2cf5b';
    return '#6fbf73';
  }

  if (value >= 0.35) return '#2f855a';
  if (value >= 0.22) return '#78b66a';
  if (value >= 0.12) return '#f2cf5b';
  return '#cf3f3f';
}

function colorByLayerValue(properties: GridAnalysisProperties, layer: LayerKey) {
  const value = getNumericProperty(properties, layer);

  if (value === null) {
    return NEUTRAL_COLOR;
  }

  if (layer === 'priority_score') return colorByScore(value);
  if (layer === 'mean_actual_lst') {
    if (value >= 41) return '#cf3f3f';
    if (value >= 38) return '#f29a4b';
    if (value >= 35) return '#f2cf5b';
    return '#6fbf73';
  }
  if (layer === 'mean_actual_anomaly') {
    if (value >= 3) return '#cf3f3f';
    if (value >= 1.5) return '#f29a4b';
    if (value >= 0) return '#f2cf5b';
    return '#6fbf73';
  }
  if (layer === 'green_ratio' || layer === 'ndvi') return colorByRatio(value, false);
  if (layer === 'building_ratio' || layer === 'impervious_ratio') return colorByRatio(value, true);
  if (layer === 'nearest_shelter_distance_m') {
    if (value <= 250) return '#2f855a';
    if (value <= 500) return '#78b66a';
    if (value <= 1000) return '#f2cf5b';
    return '#cf3f3f';
  }

  return NEUTRAL_COLOR;
}

/* ─────────────────────────────────────────────────────────────────────────
   구(區) 단위 채색은 격자용 고정 임계값을 쓰지 않는다.

   colorByScore·colorByRatio의 경계값(0.3 / 0.5 / 0.7 등)은 100m 격자 분포를 보고
   정한 값이다. 구 단위는 격자 평균이라 극단값이 상쇄돼 범위가 훨씬 좁다.
   예: 건물비율 구 범위는 0.124~0.298인데 첫 경계가 0.3 → 25개 구가 전부 같은 색.

   그래서 구 단위는 '그 화면에 실제로 그려지는 값들'에서 사분위 경계를 뽑아 쓴다.
   코로플레스 지도의 표준 분류 방식(quantile classification)이고,
   데이터가 바뀌어도 사람이 임계값을 다시 만질 필요가 없다.
   ───────────────────────────────────────────────────────────────────────── */

// 낮은 값 → 높은 값 순서. 기존 격자 팔레트와 같은 색을 쓴다.
const RISK_RAMP = ['#6fbf73', '#f2cf5b', '#f29a4b', '#cf3f3f'];      // 높을수록 나쁨
const BENEFIT_RAMP = ['#cf3f3f', '#f2cf5b', '#78b66a', '#2f855a'];   // 높을수록 좋음

/** 값이 클수록 '좋은' 지표인지 */
const HIGHER_IS_BETTER: ReadonlySet<LayerKey> = new Set<LayerKey>([
  'green_ratio',
  'ndvi'
]);

export interface QuantileBreaks {
  /** 25% / 50% / 75% 지점의 값 */
  breaks: number[];
  ramp: string[];
  /** 분류에 쓴 구의 개수 */
  count: number;
}

function quantile(sorted: number[], p: number) {
  if (sorted.length === 0) return NaN;
  const pos = (sorted.length - 1) * p;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

export function computeQuantileBreaks(
  features: Feature<Geometry>[],
  layer: LayerKey
): QuantileBreaks | null {
  const values = features
    .map((feature) => getNumericProperty(getFeatureProperties(feature), layer))
    .filter((value): value is number => value !== null)
    .sort((a, b) => a - b);

  // 표본이 너무 적으면 사분위가 의미 없다
  if (values.length < 4) return null;

  return {
    breaks: [quantile(values, 0.25), quantile(values, 0.5), quantile(values, 0.75)],
    ramp: HIGHER_IS_BETTER.has(layer) ? BENEFIT_RAMP : RISK_RAMP,
    count: values.length
  };
}

export function colorByQuantile(value: number, spec: QuantileBreaks) {
  const [q1, q2, q3] = spec.breaks;
  if (value < q1) return spec.ramp[0];
  if (value < q2) return spec.ramp[1];
  if (value < q3) return spec.ramp[2];
  return spec.ramp[3];
}

function getBoundaryStyle(
  feature: Feature<Geometry> | undefined,
  selectedDistrict: string,
  selectedLayer: LayerKey,
  isDistrictOverview: boolean,
  quantileSpec: QuantileBreaks | null
): PathOptions {
  const districtName = feature ? getDistrictName(feature) : '';
  const isSelected = districtName === selectedDistrict;
  const properties = feature ? getFeatureProperties(feature) : {};
  const layerValue = getNumericProperty(properties, selectedLayer);
  const hasLayerValue = layerValue !== null;

  if (isDistrictOverview) {
    // 구 단위는 사분위 분류를 쓴다. 사분위를 못 만들면(값이 4개 미만) 격자 규칙으로 내려간다.
    const fillColor =
      hasLayerValue && quantileSpec
        ? colorByQuantile(layerValue, quantileSpec)
        : colorByLayerValue(properties, selectedLayer);
    return {
      color: isSelected ? '#063f25' : '#1f2933',
      fillColor,
      fillOpacity: hasLayerValue ? (isSelected ? 0.68 : 0.5) : 0.18,
      opacity: isSelected ? 1 : 0.72,
      weight: isSelected ? 3.2 : 1.4
    };
  }

  return {
    color: isSelected ? '#0a7a3d' : '#1f2933',
    fillColor: isSelected ? '#b7e4cf' : '#ffffff',
    fillOpacity: isSelected ? 0.1 : 0.02,
    opacity: isSelected ? 1 : 0.5,
    weight: isSelected ? 3.2 : 1.5
  };
}

function makeDistrictLabel(district: DistrictOption) {
  return L.divIcon({
    className: 'districtLabelIcon',
    html: `<span class="districtLabelName">${district.district}</span>`,
    iconSize: [92, 24],
    iconAnchor: [46, 12]
  });
}

// geojson 좌표는 숫자/문자 어느 쪽으로도 올 수 있어 유한수만 통과시킨다
function toFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

// 선택 격자의 '가장 가까운 무더위쉼터'를 지도 핀으로 찍기 위한 정보 (좌표 없으면 null)
function getShelterPin(properties: GridAnalysisProperties | null) {
  if (!properties) return null;
  const lat = toFiniteNumber(properties.nearest_shelter_lat);
  const lon = toFiniteNumber(properties.nearest_shelter_lon);
  if (lat === null || lon === null) return null;
  return {
    position: [lat, lon] as [number, number],
    name: properties.nearest_shelter_name ?? '무더위쉼터',
    addr: properties.nearest_shelter_addr ?? '',
    distance: toFiniteNumber(properties.nearest_shelter_distance_m)
  };
}

// 쉼터 핀 아이콘 (divIcon — 자치구 라벨과 동일 패턴). 스타일은 styles.css .shelterPinIcon
//
// 박스를 40x40으로 잡은 이유: .shelterPinDot은 28x28을 -45도 회전한 것이라 대각선이
// 28*sqrt(2) = 39.6px다. 예전 28x34 박스로는 그림이 밖으로 삐져나왔고, 삐져나온 부분을
// 클릭하면 아래 격자 레이어로 통과해 엉뚱한 격자가 선택됐다.
//
// 앵커 [20,40]: CSS에서 span을 박스 중앙(20,20)에 놓는다. 회전 후 뾰족한 모서리는
// 중앙에서 아래로 19.8px 지점(20, 39.8)에 오므로, 그 끝이 실제 좌표에 닿게 맞춘 값이다.
// tooltipAnchor는 핀 꼭대기(앵커에서 위로 약 40px) 위에 라벨이 뜨도록 잡았다.
const SHELTER_PIN_ICON = L.divIcon({
  className: 'shelterPinIcon',
  html: '<span class="shelterPinDot"><i>🏠</i></span>',
  iconSize: [40, 40],
  iconAnchor: [20, 40],
  popupAnchor: [0, -38],
  tooltipAnchor: [0, -40]
});

function buildDistrictTooltip(feature: Feature<Geometry>, selectedLayer: LayerKey) {
  const properties = getFeatureProperties(feature);
  const district = getDistrictName(feature);

  return `
    <div class="districtTempTooltipBody">
      <strong>${district}</strong>
      <span>${getLayerLabel(selectedLayer)} ${formatLayerProperty(properties, selectedLayer)}</span>
      <small>구 단위 ML 속성은 향후 GeoJSON properties로 제공 예정</small>
    </div>
  `;
}

function gridFeatureStyle(
  feature: Feature<Geometry>,
  layer: LayerKey,
  isDistrictOverview: boolean
): PathOptions {
  const properties = getFeatureProperties(feature);
  const hasLayerValue = getNumericProperty(properties, layer) !== null;
  const gridColor = colorByLayerValue(properties, layer);
  return {
    color: gridColor,
    fillColor: gridColor,
    fillOpacity: hasLayerValue ? 0.68 : 0.22,
    opacity: hasLayerValue ? 0.32 : 0.18,
    weight: isDistrictOverview ? 0.12 : 0.35
  };
}

function buildGridTooltip(feature: Feature<Geometry>, layer: LayerKey, gridMeters: number) {
  const properties = getFeatureProperties(feature);
  const gridSize = formatAnyProperty(properties, 'grid_size_m');
  const sizeLabel = gridSize === DATA_PENDING ? `${gridMeters}m` : `${gridSize}m`;

  return `
    <div class="districtTempTooltipBody">
      <strong>${formatAnyProperty(properties, 'gu_name')} ${sizeLabel} 격자</strong>
      <span>${getLayerLabel(layer)} ${formatLayerProperty(properties, layer)}</span>
      <small>${getGridIdentifier(properties)} · 면적 ${formatAnyProperty(properties, 'area_m2')}</small>
    </div>
  `;
}

function MapZoomWatcher({ onZoomChange }: { onZoomChange: (zoom: number) => void }) {
  const map = useMapEvents({
    zoomend: (event) => onZoomChange(event.target.getZoom())
  });

  useEffect(() => {
    onZoomChange(map.getZoom());
  }, [map, onZoomChange]);

  return null;
}

function MapFocus({ center, zoom }: { center: [number, number]; zoom: number }) {
  const map = useMap();

  useEffect(() => {
    map.setView(center, zoom, { animate: true });
  }, [center, zoom, map]);

  return null;
}

function RightDragPan() {
  const map = useMap();

  // 오른쪽 버튼 드래그 팬 (왼쪽 드래그는 Leaflet 기본값 = 항상 켜짐)
  useEffect(() => {
    const container = map.getContainer();
    let panning = false;
    let lastX = 0;
    let lastY = 0;

    const onMouseDown = (event: MouseEvent) => {
      if (event.button !== 2) return;
      panning = true;
      lastX = event.clientX;
      lastY = event.clientY;
      container.style.cursor = 'grabbing';
    };
    const onMouseMove = (event: MouseEvent) => {
      if (!panning) return;
      const dx = event.clientX - lastX;
      const dy = event.clientY - lastY;
      lastX = event.clientX;
      lastY = event.clientY;
      map.panBy([-dx, -dy], { animate: false });
    };
    const onMouseUp = () => {
      panning = false;
      container.style.cursor = '';
    };
    const onContextMenu = (event: MouseEvent) => {
      event.preventDefault();
    };

    container.addEventListener('mousedown', onMouseDown);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    container.addEventListener('contextmenu', onContextMenu);

    return () => {
      container.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      container.removeEventListener('contextmenu', onContextMenu);
    };
  }, [map]);

  return null;
}

function LoadingContent({ message }: { message: string }) {
  return (
    <div className="mapLoadingCard">
      <span className="mapLoadingSpinner" aria-hidden="true" />
      <strong>{message}</strong>
      <small>분석 격자와 지도 레이어를 준비하고 있습니다</small>
    </div>
  );
}

export function MapDashboard() {
  const [geoJson, setGeoJson] = useState<FeatureCollection | null>(null);
  const [gridGeoJson, setGridGeoJson] = useState<FeatureCollection | null>(null);
  const [selectedDistrict, setSelectedDistrict] = useState(DEFAULT_DISTRICT);
  const [selectedLayer, setSelectedLayer] = useState<LayerKey>(DEFAULT_LAYER);
  const [selectedGridResolution, setSelectedGridResolution] =
    useState<GridResolution>(DEFAULT_GRID_RESOLUTION);
  const [selectedGridProperties, setSelectedGridProperties] =
    useState<GridAnalysisProperties | null>(null);
  // 선택 격자가 속한 '구'의 전체 격자 수 (priority_rank의 분모). 100m 상세 로딩 때 채운다.
  const [selectedGridGuTotal, setSelectedGridGuTotal] = useState<number | null>(null);
  const [selected100mFeature, setSelected100mFeature] =
    useState<Feature<Geometry> | null>(null);
  const [selected100mPopupPosition, setSelected100mPopupPosition] =
    useState<L.LatLng | null>(null);
  const [isPanelOpen, setIsPanelOpen] = useState(true);
  const [activeTool, setActiveTool] = useState('지도선택');
  const activeToolRef = useRef(activeTool);
  activeToolRef.current = activeTool;   // 항상 최신 모드를 담아둠 (클릭 핸들러가 참조)
  const selectedGridIdRef = useRef<string | null>(null);   // 현재 팝업이 열린 격자 id
  const selectedGridLayerRef = useRef<L.Path | null>(null);   // 현재 선택 격자의 Leaflet 레이어
  const selectedResetRef = useRef<(() => void) | null>(null);   // 선택 격자 테두리 원복 함수
  const seoul100mMapCacheRef = useRef<FeatureCollection | null>(null);
  const district100mCacheRef = useRef(new Map<string, FeatureCollection>());
  // 같은 구의 상세 파일을 중복 요청하지 않도록 진행 중인 Promise도 공유한다.
  const district100mRequestRef = useRef(new Map<string, Promise<FeatureCollection>>());

  // Phase 3 — 격자 비교(두 번째 격자). A(주 격자)는 그대로 두고 B에 담는다.
  const [comparePropertiesB, setComparePropertiesB] =
    useState<GridAnalysisProperties | null>(null);
  const [isPickingCompare, setIsPickingCompare] = useState(false);   // 비교 격자 고르는 중?
  const isPickingCompareRef = useRef(false);
  isPickingCompareRef.current = isPickingCompare;
  const compareGridIdRef = useRef<string | null>(null);      // 비교 격자 grid_id (hydrate 가드)
  const compareGridLayerRef = useRef<L.Path | null>(null);   // 250/500m 비교 격자 레이어
  const compareResetRef = useRef<(() => void) | null>(null); // 비교 테두리 원복 함수

  // 격자를 새로 누르면 사이드바가 자동으로 펴진다     
  useEffect(() => {
    if (selectedGridProperties) {
      setIsPanelOpen(true);
    }
  }, [selectedGridProperties]);

  const [zoomLevel, setZoomLevel] = useState(SEOUL_OVERVIEW_ZOOM);
  const [loading, setLoading] = useState(true);
  const [gridLoading, setGridLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getGeoJson()
      .then((geoJsonData) => setGeoJson(geoJsonData))
      .catch((requestError: unknown) => {
        setError(requestError instanceof Error ? requestError.message : '데이터 요청 실패');
      })
      .finally(() => setLoading(false));
  }, []);

  const districts = useMemo(() => buildDistrictOptions(geoJson), [geoJson]);
  const isAllDistricts = selectedDistrict === ALL_DISTRICTS;
  const selectedDistrictMeta = districts.find((district) => district.district === selectedDistrict);
  const districtCodeByName = useMemo(
    () => new Map(districts.map((district) => [district.district, district.sigCode])),
    [districts]
  );
  const loadDistrict100mDetails = useCallback((sigCode: string, districtName: string) => {
    const cached = district100mCacheRef.current.get(sigCode);
    if (cached) return Promise.resolve(cached);

    const pending = district100mRequestRef.current.get(sigCode);
    if (pending) return pending;

    const request = getDistrictResolutionGrid('100m', sigCode, districtName)
      .then((collection) => {
        district100mCacheRef.current.set(sigCode, collection);
        return collection;
      })
      .finally(() => {
        district100mRequestRef.current.delete(sigCode);
      });

    district100mRequestRef.current.set(sigCode, request);
    return request;
  }, []);

  // 구 선택 직후 상세 속성을 선로딩해 100m 격자 클릭 시 경량→상세 전환 깜빡임을 줄인다.
  useEffect(() => {
    if (selectedGridResolution !== '100m' || isAllDistricts) return;

    const sigCode = districtCodeByName.get(selectedDistrict);
    if (!sigCode) return;

    void loadDistrict100mDetails(sigCode, selectedDistrict).catch(() => {
      // 선로딩 실패 시 격자 클릭 때 다시 요청한다.
    });
  }, [
    districtCodeByName,
    isAllDistricts,
    loadDistrict100mDetails,
    selectedDistrict,
    selectedGridResolution
  ]);

  useEffect(() => {
    if (selectedGridResolution !== '100m') return;

    selectedResetRef.current?.();
    selectedGridIdRef.current = null;
    setSelected100mFeature(null);
    setSelected100mPopupPosition(null);
    setSelectedGridProperties(null);
    const sigCode = isAllDistricts ? null : districtCodeByName.get(selectedDistrict);

    if (!isAllDistricts && !sigCode) {
      setGridGeoJson(null);
      setGridLoading(false);
      return;
    }

    let isActive = true;
    const cached = seoul100mMapCacheRef.current;

    setGridLoading(!cached);
    (cached ? Promise.resolve(cached) : getResolutionGrid('100m'))
      .then((data) => {
        if (!isActive) return;
        seoul100mMapCacheRef.current = data;
        setGridGeoJson(sigCode ? filterGridByDistrict(data, sigCode) : data);
      })
      .catch((requestError: unknown) => {
        if (!isActive) return;
        setGridGeoJson(null);
        setError(requestError instanceof Error ? requestError.message : '서울 전체 100m 격자 요청 실패');
      })
      .finally(() => {
        if (isActive) setGridLoading(false);
      });

    return () => {
      isActive = false;
    };
  }, [districtCodeByName, isAllDistricts, selectedDistrict, selectedGridResolution]);

  useEffect(() => {
    if (selectedGridResolution === '100m') return;

    selectedResetRef.current?.();
    selectedGridIdRef.current = null;
    setSelected100mFeature(null);
    setSelected100mPopupPosition(null);
    setSelectedGridProperties(null);

    if (isAllDistricts) {
      let isActive = true;

      setGridLoading(true);
      getResolutionGrid(selectedGridResolution)
        .then((data) => {
          if (isActive) {
            setGridGeoJson(data);
          }
        })
        .catch((requestError: unknown) => {
          if (isActive) {
            setGridGeoJson(null);
            setError(requestError instanceof Error ? requestError.message : '서울 전체 격자 데이터 요청 실패');
          }
        })
        .finally(() => {
          if (isActive) {
            setGridLoading(false);
          }
        });

      return () => {
        isActive = false;
      };
    }

    const sigCode = districtCodeByName.get(selectedDistrict);

    if (!sigCode) {
      setGridGeoJson(null);
      setGridLoading(false);
      return;
    }

    let isActive = true;

    setGridLoading(true);

    getDistrictResolutionGrid(selectedGridResolution, sigCode, selectedDistrict)
      .then((data) => {
        if (isActive) {
          setGridGeoJson(
            selectedGridResolution === '500m' ? filterGridByDistrict(data, sigCode) : data
          );
        }
      })
      .catch((requestError: unknown) => {
        if (isActive) {
          setGridGeoJson(null);
          setError(requestError instanceof Error ? requestError.message : '격자 데이터 요청 실패');
        }
      })
      .finally(() => {
        if (isActive) {
          setGridLoading(false);
        }
      });

    return () => {
      isActive = false;
    };
  }, [
    districtCodeByName,
    isAllDistricts,
    selectedDistrict,
    selectedGridResolution
  ]);

  const center = selectedDistrictMeta?.center ?? DEFAULT_CENTER;
  const targetZoom = isAllDistricts ? SEOUL_OVERVIEW_ZOOM : DISTRICT_DETAIL_ZOOM;
  const isDistrictOverview = isAllDistricts || zoomLevel <= DISTRICT_OVERVIEW_ZOOM;

  // 구 단위 사분위 경계. 지금 화면에 그려지는 구들의 값에서 직접 뽑는다.
  const districtQuantiles = useMemo(
    () =>
      geoJson
        ? computeQuantileBreaks(geoJson.features as Feature<Geometry>[], selectedLayer)
        : null,
    [geoJson, selectedLayer]
  );

  const gridCount = gridGeoJson?.features.length ?? 0;
  const gridMeters = getResolutionMeters(selectedGridResolution);
  const gridLayerKey =
    selectedGridResolution === '100m'
      ? `100m-${selectedDistrict}-grid-${gridCount}`
      : `${selectedDistrict}-${gridMeters}m-grid-${gridCount}`;

  const hydrateSelectedGridProperties = useCallback(
    (previewProperties: GridAnalysisProperties) => {
      const gridId = getGridIdentifier(previewProperties);
      const districtName =
        typeof previewProperties.gu_name === 'string' ? previewProperties.gu_name : '';
      const sigCode = String(
        previewProperties.gu_code ?? districtCodeByName.get(districtName) ?? ''
      );

      if (!gridId || !districtName || !/^\d{5}$/.test(sigCode)) return;

      loadDistrict100mDetails(sigCode, districtName)
        .then((collection) => {
          if (selectedGridIdRef.current !== gridId) return;
          // 이 구별 상세 파일의 격자 수 = 구 내부 순위(priority_rank)의 분모
          setSelectedGridGuTotal(collection.features.length);
          const fullFeature = collection.features.find(
            (feature) => getGridIdentifier(getFeatureProperties(feature as Feature<Geometry>)) === gridId
          );
          if (fullFeature) {
            setSelectedGridProperties(
              getFeatureProperties(fullFeature as Feature<Geometry>)
            );
          }
        })
        .catch(() => {
          // 지도용 속성만으로도 선택과 팝업은 유지한다. 상세 요청은 다음 선택 시 재시도한다.
        });
    },
    [districtCodeByName, loadDistrict100mDetails]
  );
  // 비교 격자 100m 상세 보충 (A의 hydrate와 같은 캐시 사용, guTotal은 불필요)
  const hydrateCompareProperties = useCallback(
    (previewProperties: GridAnalysisProperties) => {
      const gridId = getGridIdentifier(previewProperties);
      const districtName =
        typeof previewProperties.gu_name === 'string' ? previewProperties.gu_name : '';
      const sigCode = String(
        previewProperties.gu_code ?? districtCodeByName.get(districtName) ?? ''
      );
      if (!gridId || !districtName || !/^\d{5}$/.test(sigCode)) return;

      loadDistrict100mDetails(sigCode, districtName)
        .then((collection) => {
          if (compareGridIdRef.current !== gridId) return;
          const fullFeature = collection.features.find(
            (feature) =>
              getGridIdentifier(getFeatureProperties(feature as Feature<Geometry>)) === gridId
          );
          if (fullFeature) {
            setComparePropertiesB(getFeatureProperties(fullFeature as Feature<Geometry>));
          }
        })
        .catch(() => {
          // 경량 속성만으로도 비교표 대부분은 채워진다. 다음 선택 시 재시도.
        });
    },
    [districtCodeByName, loadDistrict100mDetails]
  );
  const selected100mGridId = selected100mFeature
    ? getGridIdentifier(getFeatureProperties(selected100mFeature))
    : null;
  const compareGridId = comparePropertiesB ? getGridIdentifier(comparePropertiesB) : null;
  const handle100mFeatureClick = useCallback(
    (feature: Feature<Geometry>, latLng: L.LatLng) => {
      const properties = getFeatureProperties(feature);
      const gridId = getGridIdentifier(properties);
      const districtName = typeof properties.gu_name === 'string' ? properties.gu_name : '';
      const sigCode = String(properties.gu_code ?? districtCodeByName.get(districtName) ?? '');
      const cached = /^\d{5}$/.test(sigCode)
        ? district100mCacheRef.current.get(sigCode)
        : undefined;
      const fullFeature = cached?.features.find(
        (candidate) =>
          getGridIdentifier(getFeatureProperties(candidate as Feature<Geometry>)) === gridId
      );
      const fullProperties = fullFeature
        ? getFeatureProperties(fullFeature as Feature<Geometry>)
        : null;

      // 비교 픽 모드: B에 담고 A(주 격자)는 그대로 둔다
      if (isPickingCompareRef.current) {
        if (gridId === selectedGridIdRef.current) return;   // 같은 격자끼리 비교 불가
        compareGridIdRef.current = gridId;
        setComparePropertiesB(fullProperties ?? properties);
        setIsPickingCompare(false);
        if (!fullProperties) hydrateCompareProperties(properties);
        return;
      }

      selectedResetRef.current?.();
      selectedGridIdRef.current = gridId;
      selectedGridLayerRef.current = null;
      setSelected100mFeature(feature);
      setSelected100mPopupPosition(latLng);
      // 상세 캐시가 있으면 경량 속성을 거치지 않고 완성된 속성을 한 번에 반영한다.
      setSelectedGridProperties(fullProperties ?? properties);
      setSelectedGridGuTotal(cached?.features.length ?? null);
      if (!fullProperties) hydrateSelectedGridProperties(properties);
    },
    [districtCodeByName, hydrateSelectedGridProperties, hydrateCompareProperties]
  );
  const build100mTooltip = useCallback(
    (feature: Feature<Geometry>) => buildGridTooltip(feature, selectedLayer, 100),
    [selectedLayer]
  );
  const selectedLayerKeyRef = useRef(selectedLayer);
  const isDistrictOverviewRef = useRef(isDistrictOverview);
  const gridResolutionRef = useRef(selectedGridResolution);
  const hydrateSelectedGridPropertiesRef = useRef(hydrateSelectedGridProperties);
  selectedLayerKeyRef.current = selectedLayer;
  isDistrictOverviewRef.current = isDistrictOverview;
  gridResolutionRef.current = selectedGridResolution;
  hydrateSelectedGridPropertiesRef.current = hydrateSelectedGridProperties;
  const gridGeoJsonRef = useRef(gridGeoJson);
  gridGeoJsonRef.current = gridGeoJson;   // 현재 화면에 로드된 격자들 (250/500m 분모 계산용)

  const handleGridClick = useCallback((event: L.LeafletMouseEvent) => {
    if (activeToolRef.current !== '지도선택') return;
    if (event.originalEvent && event.originalEvent.button !== 0) return;
    const layer = (event.target ?? event.sourceTarget) as L.Path & {
      feature?: Feature<Geometry>;
    };
    if (!layer.feature) return;
    const feature = layer.feature;
    const props = getFeatureProperties(feature);

    const gridId = getGridIdentifier(props);
    const resolution = gridResolutionRef.current;
    const meters = getResolutionMeters(resolution);

    // 비교 픽 모드: B에 담고 파란 테두리만 입힌다 (A의 선택/팝업은 건드리지 않음)
    if (isPickingCompareRef.current) {
      if (gridId === selectedGridIdRef.current) return;   // 같은 격자끼리 비교 불가
      compareResetRef.current?.();
      layer.setStyle({ color: '#1c7ed6', weight: 3, opacity: 1 });
      compareGridLayerRef.current = layer;
      compareGridIdRef.current = gridId;
      setComparePropertiesB(props);
      setIsPickingCompare(false);
      compareResetRef.current = () => {
        compareGridLayerRef.current = null;
        compareResetRef.current = null;
        layer.setStyle(
          gridFeatureStyle(feature, selectedLayerKeyRef.current, isDistrictOverviewRef.current)
        );
      };
      return;
    }

    selectedResetRef.current?.();
    layer.setStyle({ color: '#111827', weight: 3, opacity: 1 });
    selectedGridLayerRef.current = layer;
    setSelectedGridProperties(props);
    setSelectedGridGuTotal(null);   // 100m면 아래 hydrate가 다시 채운다
    selectedGridIdRef.current = gridId;
    if (resolution === '100m') {
      hydrateSelectedGridPropertiesRef.current(props);
    } else {
      // 250/500m: priority_rank는 구 내부 순위 → 같은 구의 그 해상도 격자 수가 분모
      const guKey = props.gu_code ?? props.gu_name;
      const feats = gridGeoJsonRef.current?.features ?? [];
      const total =
        guKey != null
          ? feats.filter((f) => {
              const p = getFeatureProperties(f as Feature<Geometry>);
              return (p.gu_code ?? p.gu_name) === guKey;
            }).length
          : feats.length;
      setSelectedGridGuTotal(total || null);
    }
    layer
      .bindPopup(buildGridTooltip(feature, selectedLayerKeyRef.current, meters), {
        className: 'gridTempTooltip',
        autoPan: false,
        closeOnClick: false
      })
      .openPopup();

    selectedResetRef.current = () => {
      selectedGridIdRef.current = null;
      selectedGridLayerRef.current = null;
      selectedResetRef.current = null;
      layer.setStyle(
        gridFeatureStyle(feature, selectedLayerKeyRef.current, isDistrictOverviewRef.current)
      );
      layer.closePopup();
      layer.unbindPopup();
      setSelectedGridProperties(null);
    };

    layer.once('popupclose', () => {
      if (selectedGridIdRef.current !== gridId) return;
      selectedGridIdRef.current = null;
      selectedGridLayerRef.current = null;
      selectedResetRef.current = null;
      layer.setStyle(
        gridFeatureStyle(feature, selectedLayerKeyRef.current, isDistrictOverviewRef.current)
      );
      layer.unbindPopup();
      setSelectedGridProperties(null);
    });
  }, []);

  // style 함수를 고정(useCallback)해야 리렌더 때 react-leaflet이 전체를 다시 칠하지 않음
  // (안 그러면 선택 격자에 준 테두리가 리렌더마다 지워짐)
  const gridStyle = useCallback(
    (feature?: Feature<Geometry>) =>
      gridFeatureStyle(feature as Feature<Geometry>, selectedLayer, isDistrictOverview),
    [selectedLayer, isDistrictOverview]
  );

  // 지표/줌 변경으로 전체 격자 스타일이 갱신된 뒤에도 선택 테두리는 유지한다.
  useEffect(() => {
    if (!selectedGridIdRef.current || !selectedGridLayerRef.current) return;
    selectedGridLayerRef.current.setStyle({ color: '#111827', weight: 3, opacity: 1 });
  }, [gridStyle]);

  // 250/500m 비교 격자의 파란 테두리도 스타일 갱신 후 다시 입힌다.
  useEffect(() => {
    if (!compareGridIdRef.current || !compareGridLayerRef.current) return;
    compareGridLayerRef.current.setStyle({ color: '#1c7ed6', weight: 3, opacity: 1 });
  }, [gridStyle]);

  // A(주 격자) 선택이 사라지면 비교 격자도 함께 정리한다 (기준 없는 비교는 의미 없음).
  useEffect(() => {
    if (selectedGridProperties) return;
    compareResetRef.current?.();
    compareGridIdRef.current = null;
    setComparePropertiesB(null);
    setIsPickingCompare(false);
  }, [selectedGridProperties]);

  const handleStartCompare = useCallback(() => {
    setIsPickingCompare(true);
    setActiveTool('지도선택');   // 비교 격자를 클릭으로 고를 수 있게 선택 모드로
  }, []);

  const handleClearCompare = useCallback(() => {
    compareResetRef.current?.();
    compareGridIdRef.current = null;
    setComparePropertiesB(null);
    setIsPickingCompare(false);
  }, []);

  // AI 채팅 문맥. 상세 패널 안에 있던 계산을 여기로 올렸다(#26).
  // AI Tool 문맥에는 ML 데이터셋의 실제 100m grid_id만 전달한다.
  // display_grid_id는 헤더 표시용이며 API 문맥으로 승격하지 않는다.
  const chatGridId =
    selectedGridResolution === '100m' &&
    typeof selectedGridProperties?.grid_id === 'string' &&
    selectedGridProperties.grid_id.trim()
      ? selectedGridProperties.grid_id.trim()
      : null;
  const chatDisplayGridId =
    (selectedGridProperties?.display_grid_id ??
      selectedGridProperties?.grid_id ??
      '') || null;
  const chatGuName =
    typeof selectedGridProperties?.gu_name === 'string' &&
    selectedGridProperties.gu_name.trim()
      ? selectedGridProperties.gu_name.trim()
      : null;

  return (
    <div className={isPanelOpen ? 'gisShell panelOpen' : 'gisShell'}>
      {loading && (
        <div className="gisState">
          <LoadingContent message="지도 데이터 로딩 중" />
        </div>
      )}
      {error && <div className="gisState errorText">{error}</div>}
      {!loading && !error && geoJson && (
        <MapContainer
          center={center}
          zoom={SEOUL_OVERVIEW_ZOOM}
          minZoom={11}
          className="gisMap"
          zoomControl={false}
          scrollWheelZoom
          preferCanvas
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <MapZoomWatcher onZoomChange={setZoomLevel} />
          <MapFocus center={center} zoom={targetZoom} />
          <RightDragPan />
          <GeoJSON
            key={`${selectedDistrict}-${selectedLayer}-${isDistrictOverview}`}
            data={geoJson}
            interactive={selectedGridResolution !== '100m' && isDistrictOverview}
            style={(feature) =>
              getBoundaryStyle(
                feature as Feature<Geometry>,
                selectedDistrict,
                selectedLayer,
                isDistrictOverview,
                districtQuantiles
              )
            }
            onEachFeature={(feature, layer) => {
              const typedFeature = feature as Feature<Geometry>;
              const districtName = getDistrictName(typedFeature);

              if (isDistrictOverview) {
                layer.bindTooltip(buildDistrictTooltip(typedFeature, selectedLayer), {
                  className: 'districtTempTooltip',
                  direction: 'top',
                  opacity: 1,
                  sticky: true
                });
              }

              layer.on({
                click: () => setSelectedDistrict(districtName),
                mouseover: () => {
                  if (isDistrictOverview) {
                    (layer as L.Path).setStyle({
                    fillOpacity: 0.32,
                      opacity: 1,
                      weight: 3.4
                    });
                  }
                },
                mouseout: () => {
                  if (isDistrictOverview) {
                    (layer as L.Path).setStyle(
                      getBoundaryStyle(
                        typedFeature,
                        selectedDistrict,
                        selectedLayer,
                        isDistrictOverview,
                        districtQuantiles
                      )
                    );
                  }
                }
              });
            }}
          />
          {gridGeoJson && selectedGridResolution === '100m' && (
            <CanvasGridLayer
              data={gridGeoJson}
              activeTool={activeTool}
              selectedGridId={selected100mGridId}
              compareGridId={compareGridId}
              style={gridStyle}
              tooltip={build100mTooltip}
              onFeatureClick={handle100mFeatureClick}
            />
          )}
          {gridGeoJson && selectedGridResolution !== '100m' && (
            <GeoJSON
              key={gridLayerKey}
              data={gridGeoJson}
              interactive
              style={gridStyle}
              onEachFeature={(_feature, layer) => {
                layer.on('click', handleGridClick);
              }}
              eventHandlers={{
                // hover: 마우스 올린 격자에만 그때그때 툴팁 (미리 6만 개 만들지 않음)
                mouseover: (event) => {
                  const layer = event.sourceTarget;
                  if (!layer?.feature) return;
                  layer
                    .bindTooltip(buildGridTooltip(layer.feature, selectedLayer, gridMeters), {
                      className: 'gridTempTooltip',
                      direction: 'top',
                      opacity: 1,
                      sticky: true
                    })
                    .openTooltip();
                  // 주황 테두리: 지도선택 모드 & 선택(검정)/비교(파랑) 격자가 아닐 때만
                  if (
                    activeToolRef.current === '지도선택' &&
                    selectedGridLayerRef.current !== layer &&
                    compareGridLayerRef.current !== layer
                  ) {
                    (layer as L.Path).setStyle({ color: '#e8590c', weight: 2.5, opacity: 1 });
                  }
                },
                mouseout: (event) => {
                  const layer = event.sourceTarget;
                  layer?.unbindTooltip?.();
                  // 선택(검정)·비교(파랑) 격자면 테두리 유지, 아니면 원래 스타일로 복원
                  if (
                    layer &&
                    selectedGridLayerRef.current !== layer &&
                    compareGridLayerRef.current !== layer
                  ) {
                    (layer as L.Path).setStyle(
                      gridFeatureStyle(layer.feature, selectedLayer, isDistrictOverview)
                    );
                  }
                }
              }}
            />
          )}
          {selectedGridResolution === '100m' &&
            selected100mFeature &&
            selected100mPopupPosition &&
            selected100mGridId && (
              <Popup
                key={selected100mGridId}
                position={selected100mPopupPosition}
                className="gridTempTooltip"
                autoPan={false}
                closeOnClick={false}
                eventHandlers={{
                  remove: () => {
                    if (selectedGridIdRef.current !== selected100mGridId) return;
                    selectedGridIdRef.current = null;
                    setSelected100mFeature(null);
                    setSelected100mPopupPosition(null);
                    setSelectedGridProperties(null);
                  }
                }}
              >
                <div
                  dangerouslySetInnerHTML={{
                    __html: build100mTooltip(selected100mFeature)
                  }}
                />
              </Popup>
            )}
          {districts.map((district) => (
            <Marker
              key={district.district}
              position={district.labelCenter}
              icon={makeDistrictLabel(district)}
              eventHandlers={{ click: () => setSelectedDistrict(district.district) }}
            />
          ))}
          {(() => {
            // 선택 격자의 가장 가까운 쉼터를 지도에 핀으로 표시 (100m는 hydrate 후 좌표가 채워짐)
            const shelter = getShelterPin(selectedGridProperties);
            if (!shelter) return null;
            return (
              <Marker
                position={shelter.position}
                icon={SHELTER_PIN_ICON}
                alt={`무더위쉼터 ${shelter.name}`}
              >
                {/* 항상 보이는 라벨.
                    hover로 띄우면 두 가지가 걸린다. (1) 격자 hover 툴팁이 sticky라 마우스를
                    따라다니며 이 라벨을 덮는다. (2) 핀 그림(-45도 회전한 28x28)의 대각선이
                    아이콘 클릭영역(28x34) 밖으로 나와, 삐져나온 부분을 클릭하면 아래 격자
                    레이어로 통과해 다른 격자가 선택된다.
                    항상 띄워두면 hover도 클릭도 필요 없어 둘 다 우회한다. */}
                <Tooltip className="shelterTip" direction="top" opacity={1} permanent>
                  <b>무더위쉼터</b>
                  <span>{shelter.name}</span>
                  {shelter.distance !== null && (
                    <span className="stDist">
                      여기서 약 {Math.round(shelter.distance).toLocaleString()}m
                    </span>
                  )}
                </Tooltip>
                <Popup className="shelterPopup" autoPan={false}>
                  <strong>{shelter.name}</strong>
                  {shelter.addr && <span>{shelter.addr}</span>}
                  {shelter.distance !== null && (
                    <span>선택 격자에서 약 {Math.round(shelter.distance)}m</span>
                  )}
                </Popup>
              </Marker>
            );
          })()}
          {/* 줌·축척을 우측 상단 도구 팔레트(.rightToolbar) 옆으로 모았다.
              지도 조작 컨트롤이 화면 양 끝에 흩어져 있으면 시선이 두 번 움직인다. */}
          <ZoomControl position="topright" />
          {/* 카드 안쪽 폭이 50px이라 maxWidth를 40으로 둔다.
              50으로 맞추면 막대가 좌우 여백 없이 꽉 차서 넘친 것처럼 보인다. */}
          <ScaleControl position="topright" metric imperial={false} maxWidth={40} />
        </MapContainer>
      )}
      {!loading && !error && gridLoading && (
        <div className="mapLoadingOverlay">
          <LoadingContent message={`${selectedGridResolution} 격자 데이터 로딩 중`} />
        </div>
      )}
      <SearchPanel
        districts={districts}
        selectedDistrict={selectedDistrict}
        selectedLayer={selectedLayer}
        selectedGridResolution={selectedGridResolution}
        gridCount={gridCount}
        gridLoading={gridLoading}
        selectedGridProperties={selectedGridProperties}
        onDistrictChange={setSelectedDistrict}
        onGridResolutionChange={setSelectedGridResolution}
        onLayerChange={setSelectedLayer}
      />
      <RightToolbar activeTool={activeTool} onSelectTool={setActiveTool} />
      <GridDetailSidePanel
        properties={selectedGridProperties}
        guGridTotal={selectedGridGuTotal}
        selectedDistrict={selectedDistrict}
        selectedGridResolution={selectedGridResolution}
        isOpen={isPanelOpen}
        onToggle={() => setIsPanelOpen((prev) => !prev)}
        formatValue={formatAnyProperty}
        compareProperties={comparePropertiesB}
        isPickingCompare={isPickingCompare}
        onStartCompare={handleStartCompare}
        onClearCompare={handleClearCompare}
      />
      {/* 색 범례. colorByLayerValue를 그대로 넘겨 범례가 지도와 같은 색을 쓰게 한다. */}
      {!loading && !error && (
        <MapLegend
          layer={selectedLayer}
          layerLabel={getLayerLabel(selectedLayer)}
          colorOf={colorByLayerValue}
          neutralColor={NEUTRAL_COLOR}
          quantile={isDistrictOverview ? districtQuantiles : null}
        />
      )}
      <AiChatLauncher
        selectedGridId={chatGridId}
        selectedDisplayGridId={chatDisplayGridId}
        selectedGuName={chatGuName}
      />
    </div>
  );
}

interface SearchPanelProps {
  districts: DistrictOption[];
  selectedDistrict: string;
  selectedLayer: LayerKey;
  selectedGridResolution: GridResolution;
  gridCount: number;
  gridLoading: boolean;
  selectedGridProperties: GridAnalysisProperties | null;
  onDistrictChange: (district: string) => void;
  onGridResolutionChange: (resolution: GridResolution) => void;
  onLayerChange: (layer: LayerKey) => void;
}

function SearchPanel({
  districts,
  selectedDistrict,
  selectedLayer,
  selectedGridResolution,
  gridCount,
  gridLoading,
  selectedGridProperties,
  onDistrictChange,
  onGridResolutionChange,
  onLayerChange
}: SearchPanelProps) {
  // 지표 버튼 위 커스텀 툴팁 (스크롤 패널에 잘리지 않게 position:fixed로 화면 기준 표시)
  const [tip, setTip] = useState<{ text: string; top: number; left: number } | null>(null);
  const showTip = (event: ReactMouseEvent | ReactFocusEvent, text: string) => {
    const rect = event.currentTarget.getBoundingClientRect();
    setTip({
      text,
      top: rect.top + rect.height / 2,
      left: rect.right + 12
    });
  };
  const hideTip = () => setTip(null);

  const selectionPrompt =
    selectedDistrict === ALL_DISTRICTS && selectedGridResolution !== '100m'
      ? '지도에서 분석할 지역을 클릭하세요'
      : `${selectedDistrict === ALL_DISTRICTS ? '지도' : selectedDistrict}에서 격자를 클릭하세요`;

  return (
    <aside className="gisLeftPanel heatIslandPanel">
      <div className="heatPanelHeader">
        <p>Urban Heat Island</p>
        <h1>도시 열섬 해결 대시보드</h1>
      </div>

      <div className="heatControlBlock">
        <label>
          <span>분석 지역</span>
          <select
            value={selectedDistrict}
            onChange={(event) => onDistrictChange(event.target.value)}
            aria-label="자치구"
          >
            <option value={ALL_DISTRICTS}>{ALL_DISTRICTS}</option>
            {districts.map((district) => (
              <option key={district.district} value={district.district}>
                {district.district}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>분석 기준</span>
          <div className="fixedGridSize">{ANALYSIS_PERIOD_LABEL}</div>
        </label>
        <label>
          <span>격자 크기</span>
          <select
            value={selectedGridResolution}
            onChange={(event) => onGridResolutionChange(event.target.value as GridResolution)}
            aria-label="격자 해상도"
          >
            {GRID_RESOLUTION_OPTIONS.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {!selectedGridProperties && (
        <div className="selectionGuide" role="status">
          <span className="sgIcon" aria-hidden="true">📍</span>
          <span className="sgText">{selectionPrompt}</span>
        </div>
      )}

      <section className="indicatorPanel">
        <div className="indicatorHeader">
          <strong>지도 지표 선택</strong>
          <span>{getLayerLabel(selectedLayer)}</span>
        </div>
        <div className="indicatorList">
          {HEAT_ISLAND_INDICATORS.map((indicator) => (
            <button
              key={indicator.key}
              type="button"
              className={selectedLayer === indicator.key ? 'active' : ''}
              onClick={() => onLayerChange(indicator.key)}
              onMouseEnter={(event) => showTip(event, indicator.desc)}
              onMouseLeave={hideTip}
              onFocus={(event) => showTip(event, indicator.desc)}
              onBlur={hideTip}
            >
              <span>
                <strong>
                  {indicator.label}
                  <i className="indicatorInfo" aria-hidden="true">ⓘ</i>
                </strong>
                <small>{indicator.help}</small>
              </span>
              <b>
                {selectedGridProperties
                  ? formatLayerProperty(selectedGridProperties, indicator.key)
                  : '선택 전'}
              </b>
            </button>
          ))}
        </div>
      </section>

      {/* 안내문(미선택)과 푸터(선택)는 상호 배타 → 패널 높이를 일정하게 유지(스크롤 방지) */}
      {selectedGridProperties && (
        <p className="panelFootNote">{noticeText(selectedDistrict, selectedGridResolution)}</p>
      )}

      {tip && (
        <div className="indicatorTip" style={{ top: tip.top, left: tip.left }} role="tooltip">
          {tip.text}
        </div>
      )}
    </aside>
  );
}

function RightToolbar({
  activeTool,
  onSelectTool
}: {
  activeTool: string;
  onSelectTool: (tool: string) => void;
}) {
  const tools = ['지도선택', '이동'];

  return (
    <nav className="rightToolbar" aria-label="지도 조작 도구">
      {tools.map((tool) => (
        <button
          key={tool}
          type="button"
          className={tool === activeTool ? 'active' : undefined}
          onClick={() => onSelectTool(tool)}
        >
          <span>{tool === '지도선택' ? '▰' : tool === '이동' ? '✥' : tool === '거리' ? '↔' : tool === '면적' ? '▣' : tool === '지우개' ? '⌫' : '▤'}</span>
          {tool}
        </button>
      ))}
    </nav>
  );
}

// 왼쪽 패널 맨 아래 푸터 문구 (지도 위 별도 바 대신 패널 안에 둔다)
function noticeText(selectedDistrict: string, selectedGridResolution: GridResolution) {
  if (selectedGridResolution === '100m') {
    const scope =
      selectedDistrict === ALL_DISTRICTS ? '서울 전체' : selectedDistrict;
    return `${scope} 100m 격자예요. 지도를 움직여도 선택한 격자는 유지됩니다.`;
  }
  return '분석값이 없는 격자는 "데이터 준비중"으로 표시됩니다.';
}
