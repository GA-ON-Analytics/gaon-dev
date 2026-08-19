import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FocusEvent as ReactFocusEvent,
  type MouseEvent as ReactMouseEvent
} from 'react';
import { createPortal } from 'react-dom';
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
  getDongSearchIndex,
  getGeoJson,
  getResolutionGrid
} from '../services/api';
import type {
  BatchSimulationResponse,
  DongSearchIndexEntry,
  GridAnalysisProperties,
  GridResolution,
  LayerKey
} from '../types/dashboard';
import GridDetailSidePanel from './GridDetailSidePanel';
import OnboardingGuide, { shouldShowOnboarding } from './OnboardingGuide';
import AiChatLauncher from './AiChatLauncher';
import MapLegend from './MapLegend';
import CanvasGridLayer, { featureContainsPoint } from './CanvasGridLayer';
import Map3DOverlay from '../features/map3d/Map3DOverlay';
import type { Map3DViewMode } from '../features/map3d/Heat3DMap';

const ALL_DISTRICTS = '전체';
const DEFAULT_DISTRICT = ALL_DISTRICTS;
const DEFAULT_LAYER: LayerKey = 'priority_score';
const LST_LAYER: LayerKey = 'mean_actual_lst';
const DEFAULT_GRID_RESOLUTION: GridResolution = '100m';
const DEFAULT_CENTER: [number, number] = [37.5665, 126.978];
const SEOUL_OVERVIEW_ZOOM = 11;
const DISTRICT_DETAIL_ZOOM = 13;
const DISTRICT_OVERVIEW_ZOOM = 12;
const DONG_SEARCH_FALLBACK_ZOOM = 15;
// 격자 코드로 찾아갔을 때의 줌. 100m 격자 하나가 화면에서 식별되려면 구 상세(13)로는 부족하다.
const GRID_SEARCH_ZOOM = 17;
const MOBILE_LAYOUT_QUERY = '(max-width: 820px)';
// 현재 제공되는 격자 코드 형식: 자치구 5자리 + '_' + 격자 5자리 (예: 11560_02332)
const GRID_CODE_PATTERN = /^(\d{5})_(\d{5})$/;
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

type LocationSearchResult =
  | { type: 'district'; district: DistrictOption }
  | { type: 'dong'; dong: DongSearchIndexEntry };

type AggregateGridResolution = '250m' | '500m';

type GridFeatureLayer = L.Path & {
  feature?: Feature<Geometry>;
};

interface AggregateGridSearchHit {
  feature: Feature<Geometry>;
  resolution: AggregateGridResolution;
  district: string;
}

interface PolicyBatchContext {
  resolution: GridResolution;
  district: string;
  selectedGridId: string | null;
}

interface LayerOption {
  key: LayerKey;
  group: 'decision' | 'environment';
  label: string;
  help: string;   // 버튼 안에 보이는 짧은 설명
  desc: string;   // 마우스 올리면 뜨는 상세 툴팁
}

interface GridResolutionOption {
  key: GridResolution;
  label: string;
}

// 지표 설명 끝에 출처·기준 시점을 붙인다. "이 숫자를 어디서 가져왔나"에 답할 수 없으면
// 위성 추정값을 실측처럼 읽는다. 출처는 GAON/docs/GAON_ML_전과정_정리_ko.md §4.2 기준.
const SAT_PERIOD = '\n기준 · 2023~2025년 여름(6~8월) 관측 평균';
const LST_SOURCE = '\n\n출처 · 위성 Landsat 8/9 열적외 밴드(ST_B10)' + SAT_PERIOD;
const DW_SOURCE = '\n\n출처 · 위성 Dynamic World (Google Earth Engine)' + SAT_PERIOD;

const HEAT_ISLAND_INDICATORS: LayerOption[] = [
  {
    key: 'priority_score',
    group: 'decision',
    label: '개선 우선순위',
    help: '종합 점수 0~100',
    // 가중치는 GAON_ML_전과정_정리_ko.md 6.6절 표에서 옮겼다. 실제 계산은 ML 레포의
    // build_seoul_dashboard.py에 있으므로, 가중치를 조정하면 이 문구도 함께 고쳐야 한다.
    desc:
      '녹지·불투수면·온도 등을 종합해 개선이 시급한 정도를 0~100으로 매긴 점수예요. 높을수록 우선 대상.' +
      '\n\n판단 기준(가중치)' +
      '\n· 열 위험 30% · 냉각 여지 20%  ← 물리' +
      '\n· 취약계층(고령인구) 25%  ← 사람' +
      '\n· 쉼터 접근성 15%  ← 정책 갭' +
      '\n· 녹지 부족 5% · 포장면 5%  ← 환경' +
      '\n\n각 항목을 구 안에서 매긴 백분위로 바꾼 뒤 위 가중치로 합칩니다. ' +
      '구가 다르면 직접 비교할 수 없어요.'
  },
  {
    key: 'mean_actual_anomaly',
    group: 'decision',
    label: '현재 열 위험',
    help: '구 평균 대비 온도차',
    desc:
      '같은 자치구 평균보다 이 격자가 얼마나 더 뜨거운지(℃)를 보여줘요. 양수일수록 더 더운 지역.' +
      LST_SOURCE
  },
  {
    key: 'nearest_shelter_distance_m',
    group: 'decision',
    label: '쉼터 접근성',
    help: '가장 가까운 쉼터 거리',
    desc:
      '가장 가까운 무더위쉼터까지의 거리(m)예요. 가까울수록 폭염 대응에 유리합니다.' +
      '\n\n격자 중심에서 잰 직선거리라 실제 도보 거리와 다를 수 있어요.' +
      '\n\n출처 · 서울 열린데이터 무더위쉼터 4,088개'
  },
  {
    key: 'mean_actual_lst',
    group: 'environment',
    label: '실제 지표면온도',
    help: '위성 관측 지표온도',
    desc: '위성으로 관측한 지표면 온도(℃)예요.' + LST_SOURCE
  },
  // '시나리오 저감효과'(green_delta_c) 레이어는 뺐다. 단일 고정 시나리오(녹지+5%p·NDVI+0.03·
  // 불투수-5%p) 하나만 지도 전체에 칠하는 방식이라, 우측 패널의 시뮬레이션 값과 어긋나 혼란을
  // 줬다. 시나리오별 저감효과는 우측 상세 패널에서 시나리오를 골라 보는 방식으로 옮긴다.
  // green_delta_c 필드 자체는 priority_score 계산에 쓰이므로 데이터에는 그대로 남아 있다.
  {
    key: 'green_ratio',
    group: 'environment',
    label: '녹지율',
    help: '식생이 덮은 비율',
    desc: '격자 면적 중 식생(풀·나무 등)이 덮은 비율이에요. 높을수록 시원한 편.' + DW_SOURCE
  },
  {
    key: 'ndvi',
    group: 'environment',
    label: '식생지수(NDVI)',
    help: '식생 활력 지수',
    desc:
      '위성 기반 식생 활력도(−1~1)예요. 값이 클수록 초록이 무성합니다.' +
      '\n\n출처 · 위성 Sentinel-2 (Google Earth Engine)' +
      SAT_PERIOD
  },
  {
    key: 'impervious_ratio',
    group: 'environment',
    label: '불투수면 비율',
    help: '아스팔트·콘크리트 비율',
    desc:
      '물이 스며들지 못하는 포장면(아스팔트·콘크리트) 비율이에요. 높을수록 더 뜨거움.' + DW_SOURCE
  },
  {
    key: 'building_ratio',
    group: 'environment',
    label: '건물 비율',
    help: '건물이 덮은 비율',
    desc:
      '격자 면적 중 건물이 차지하는 비율이에요. 높을수록 열이 쌓이기 쉬움.' +
      '\n\n출처 · 국토부 VWorld 건물 도형 (lt_c_spbd)'
  }
];

const INDICATOR_GROUPS = [
  { key: 'decision' as const, label: '핵심 판단' },
  { key: 'environment' as const, label: '환경' }
] as const;

const GRID_RESOLUTION_OPTIONS: GridResolutionOption[] = [
  { key: '100m', label: '100M' },
  { key: '250m', label: '250M' },
  { key: '500m', label: '500M' },
  // 격자가 아니라 자치구 경계 단위. 예전에는 '전체 선택 + 낮은 줌'이면 암묵적으로
  // 구 단위로 칠했는데, 격자와 겹쳐 그려져 무엇을 보고 있는지 알 수 없었다.
  // 명시적 선택지로 올려 "지금 무엇을 보고 있는가"를 사용자가 정하게 한다.
  { key: 'gu', label: '구' }
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
  if (resolution === 'gu') return 0;   // 격자가 아니다
  return Number(resolution.replace('m', ''));
}

function getGridSearchZoom(resolution: AggregateGridResolution) {
  return Math.round(GRID_SEARCH_ZOOM - Math.log2(getResolutionMeters(resolution) / 100));
}

/** 격자 대신 자치구 경계를 칠하는 모드인가 */
function isGuResolution(resolution: GridResolution) {
  return resolution === 'gu';
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

function normalizeLocationText(value: string) {
  return value.toLocaleLowerCase('ko').replace(/\s+/g, '');
}

/* 구 단위는 같은 개념을 다른 필드에 담는다.
   격자의 mean_actual_anomaly 는 '그 구 평균 대비' 편차라, 구 전체를 평균하면
   정의상 0이 된다. 그래서 ML(aggregate_dashboard_resolution.py)이 의도적으로 제외하고
   대신 gu_anomaly_vs_seoul('서울 평균 대비')을 넣어두었다.
   → 프론트가 구 단위에서는 이 필드를 읽어야 한다. */
const DISTRICT_FIELD_BY_LAYER: Partial<Record<LayerKey, string>> = {
  mean_actual_anomaly: 'gu_anomaly_vs_seoul',
  // 격자의 priority_score는 '그 구 안에서의 백분위'라 구끼리 비교할 수 없고,
  // 백분위의 평균은 정의상 50이라 구 평균도 의미가 없다.
  // 그래서 ML이 같은 가중치로 '서울 25개 구 안에서' 다시 매긴 값을 쓴다.
  priority_score: 'gu_priority_score'
};

function districtFieldOf(layer: LayerKey) {
  return DISTRICT_FIELD_BY_LAYER[layer] ?? layer;
}

/** 해상도에 맞는 필드에서 값을 읽는다. 구 단위에서는 별칭이 있으면 그쪽을 본다. */
function getLayerValue(
  properties: GridAnalysisProperties,
  layer: LayerKey,
  isDistrictOverview: boolean
) {
  return getNumericProperty(
    properties,
    isDistrictOverview ? districtFieldOf(layer) : layer
  );
}

function getNumericProperty(properties: GridAnalysisProperties, key: string) {
  const value: unknown = properties[key as keyof GridAnalysisProperties];   // geojson 값은 문자열일 수도 있어 unknown으로 받는다

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

function findDongCenterGridFeature(
  features: Feature<Geometry>[],
  dongCenter: [number, number]
) {
  const [latitude, longitude] = dongCenter;
  const containing = features.filter((feature) =>
    featureContainsPoint(feature, longitude, latitude)
  );
  const candidates = containing.length > 0 ? containing : features;

  let nearest: { feature: Feature<Geometry>; distanceSquared: number; gridId: string } | null =
    null;
  for (const feature of candidates) {
    const center = L.geoJSON(feature).getBounds().getCenter();
    const dx = center.lng - longitude;
    const dy = center.lat - latitude;
    const distanceSquared = dx * dx + dy * dy;
    const gridId = String(getGridIdentifier(getFeatureProperties(feature)));

    if (
      nearest === null ||
      distanceSquared < nearest.distanceSquared ||
      (distanceSquared === nearest.distanceSquared && gridId < nearest.gridId)
    ) {
      nearest = { feature, distanceSquared, gridId };
    }
  }

  return nearest?.feature ?? null;
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

function formatLayerProperty(
  properties: GridAnalysisProperties | null,
  layer: LayerKey,
  isDistrictOverview = false
) {
  // 구 단위는 같은 개념을 다른 필드에 담는다(DISTRICT_FIELD_BY_LAYER).
  // 지도 색칠·툴팁은 이미 별칭을 쓰는데 지표 목록만 빠져 있었다.
  return formatAnyProperty(
    properties,
    (isDistrictOverview
      ? districtFieldOf(layer)
      : layer) as keyof GridAnalysisProperties
  );
}

/* ─────────────────────────────────────────────────────────────────────────
   지도 색 램프 — 모든 레이어가 이 한 벌만 쓴다.

   예전에는 램프가 두 벌이었고 서로 대칭이 아니었다.
     위험형: #6fbf73(L59) → #f2cf5b → #f29a4b → #cf3f3f
     이득형: #cf3f3f → #f2cf5b → #78b66a(L56) → #2f855a(L35)
   '가장 좋음'이 한쪽은 밝은 초록(L59), 다른 쪽은 진한 초록(L35)이라 밝기가 24%p
   차이 났고, 이득형에는 주황 단계가 아예 없었다. 그래서 레이어를 바꿀 때마다
   같은 등급이 다른 색으로 보였다.

   이제 한 벌을 두고 방향만 뒤집는다. 같은 등급 = 항상 같은 색.
   ───────────────────────────────────────────────────────────────────────── */
const HEAT_RAMP = ['#6fbf73', '#f2cf5b', '#f29a4b', '#cf3f3f'] as const;   // 좋음 → 나쁨

/** 값이 클수록 나쁘면 그대로, 값이 클수록 좋으면 뒤집는다 */
function rampFor(highIsRisk: boolean) {
  return highIsRisk ? [...HEAT_RAMP] : [...HEAT_RAMP].reverse();
}

/** 낮은 값 → 높은 값 순서의 경계로 구간 색을 고른다 */
function pickByBreaks(value: number, breaks: readonly number[], highIsRisk: boolean) {
  const ramp = rampFor(highIsRisk);
  for (let i = 0; i < breaks.length; i += 1) {
    if (value < breaks[i]) return ramp[i];
  }
  return ramp[breaks.length];
}

/* 경계값은 실제 점수 분포를 보고 정했다. 서울 100m 격자 64,546개 기준
   중앙값 49.3, 상위 5%가 69.4, 최댓값이 86.5다.
   예전 경계(40/60/80)는 80점 이상이 0.2%(약 130개)뿐이라 '시급' 빨강이
   지도에서 사실상 보이지 않았고, 60~80 노랑 구간이 55%를 차지했다.
   40/55/70으로 내리면 22% / 42% / 32% / 4.4%로 네 등급이 고르게 퍼진다.
   (250m·500m 격자도 같은 경향이라 해상도별로 따로 두지 않는다) */
function colorByScore(value: number, highIsRisk = true) {
  return pickByBreaks(value, [40, 55, 70], highIsRisk);
}

function colorByRatio(value: number, highIsRisk: boolean) {
  return highIsRisk
    ? pickByBreaks(value, [0.3, 0.5, 0.7], true)
    : pickByBreaks(value, [0.12, 0.22, 0.35], false);
}

function colorByLayerValue(properties: GridAnalysisProperties, layer: LayerKey) {
  const value = getNumericProperty(properties, layer);

  if (value === null) {
    return NEUTRAL_COLOR;
  }

  if (layer === 'priority_score') return colorByScore(value);
  if (layer === 'mean_actual_lst') return pickByBreaks(value, [35, 38, 41], true);
  if (layer === 'mean_actual_anomaly') return pickByBreaks(value, [0, 1.5, 3], true);
  if (layer === 'green_ratio' || layer === 'ndvi') return colorByRatio(value, false);
  if (layer === 'building_ratio' || layer === 'impervious_ratio') return colorByRatio(value, true);
  // 쉼터는 가까울수록 좋다 = 값이 작을수록 좋다 → 위험형(값 클수록 나쁨)과 같은 방향
  if (layer === 'nearest_shelter_distance_m') {
    return pickByBreaks(value, [250, 500, 1000], true);
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
    // 구 단위 features이므로 별칭 필드를 본다
    .map((feature) => getLayerValue(getFeatureProperties(feature), layer, true))
    .filter((value): value is number => value !== null)
    .sort((a, b) => a - b);

  // 표본이 너무 적으면 사분위가 의미 없다
  if (values.length < 4) return null;

  return {
    breaks: [quantile(values, 0.25), quantile(values, 0.5), quantile(values, 0.75)],
    // 격자와 같은 램프를 쓴다. 값이 클수록 좋은 지표만 방향을 뒤집는다.
    ramp: rampFor(!HIGHER_IS_BETTER.has(layer)),
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
  isGuMode: boolean,
  quantileSpec: QuantileBreaks | null
): PathOptions {
  const districtName = feature ? getDistrictName(feature) : '';
  const isSelected = districtName === selectedDistrict;
  const properties = feature ? getFeatureProperties(feature) : {};
  const layerValue = getLayerValue(properties, selectedLayer, isGuMode);
  const hasLayerValue = layerValue !== null;

  if (isGuMode) {
    // 구 단위는 사분위 분류를 쓴다. 사분위를 못 만들면(값이 4개 미만) 격자 규칙으로 내려간다.
    const fillColor =
      hasLayerValue && quantileSpec
        ? colorByQuantile(layerValue, quantileSpec)
        : colorByLayerValue(properties, selectedLayer);
    return {
      color: isSelected ? '#063f25' : '#1f2933',
      fillColor,
      // 구 모드에서는 이 폴리곤이 유일한 데이터 표현이므로 진하게 칠한다.
      // (격자와 겹쳐 그리던 시절에는 아래가 비쳐야 해서 0.5였다)
      fillOpacity: hasLayerValue ? (isSelected ? 0.92 : 0.82) : 0.2,
      opacity: 1,
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

type ShelterPin = {
  position: [number, number];
  name: string;
  addr: string;
  distance: number | null;
};

// 선택 격자의 '가장 가까운 무더위쉼터'를 지도 핀으로 찍기 위한 정보 (좌표 없으면 null)
function getShelterPin(properties: GridAnalysisProperties | null): ShelterPin | null {
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

  // 구 단위 별칭 필드를 읽는다(예: 현재 열 위험 → gu_anomaly_vs_seoul)
  const value = formatAnyProperty(
    properties,
    districtFieldOf(selectedLayer) as keyof GridAnalysisProperties
  );
  const note =
    selectedLayer === 'mean_actual_anomaly'
      ? '서울 평균 대비 (격자 단위는 구 평균 대비)'
      : '구 25개 안에서의 상대 위치로 색을 매깁니다';

  return `
    <div class="districtTempTooltipBody">
      <strong>${district}</strong>
      <span>${getLayerLabel(selectedLayer)} ${value}</span>
      <small>${note}</small>
    </div>
  `;
}

/* 정책 적용 후 색. 3D(Heat3DMap.prepareExtrusionData)와 같은 기준을 쓴다 —
   관측 LST에 모델이 낸 delta_c를 더한 시나리오 값이며 실측 After가 아니다.
   afterDeltas가 null이면(=현재 보기) 평소의 레이어 색으로 떨어진다. */
function afterPolicyColor(
  properties: GridAnalysisProperties,
  afterDeltas: ReadonlyMap<string, number> | null
) {
  if (!afterDeltas) return null;
  const delta = afterDeltas.get(String(getGridIdentifier(properties)));
  if (delta === undefined) return null;
  const lst = getNumericProperty(properties, LST_LAYER);
  if (lst === null) return null;
  return pickByBreaks(lst + delta, [35, 38, 41], true);
}

function gridFeatureStyle(
  feature: Feature<Geometry>,
  layer: LayerKey,
  isDistrictOverview: boolean,
  afterDeltas: ReadonlyMap<string, number> | null = null
): PathOptions {
  const properties = getFeatureProperties(feature);
  const hasLayerValue = getNumericProperty(properties, layer) !== null;
  const gridColor =
    afterPolicyColor(properties, afterDeltas) ?? colorByLayerValue(properties, layer);
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

/** 라벨이 격자 팝업을 피해 내려가는 거리. styles.css 의 .shelterTipBelow 와 같아야 한다. */
const SHELTER_LABEL_SHIFT = 112;

/**
 * 쉼터 핀과 라벨.
 *
 * 라벨이 격자 정보 팝업과 겹치는 문제가 있었다. 쉼터가 가까운 격자
 * (예: 11680_03624 는 6m)에서는 팝업이 라벨을 통째로 덮었다.
 * z-index 로 올리면 이번엔 라벨이 팝업을 덮어 둘 중 하나는 못 읽는다.
 * 그래서 **겹칠 자리면 라벨을 핀 아래로 내린다**. 둘 다 읽을 수 있다.
 *
 * 판단은 라벨의 현재 위치가 아니라 '핀 기준으로 라벨이 차지할 자리'로 한다.
 * 현재 위치로 재면 내렸다가 안 겹치니 다시 올리는 진동이 생긴다.
 */
function ShelterMarker({ shelter }: { shelter: ShelterPin | null }) {
  const map = useMap();
  const shelterKey = shelter ? `${shelter.position[0]},${shelter.position[1]}` : '';

  useEffect(() => {
    if (!shelter) return;

    const update = () => {
      const label = document.querySelector('.leaflet-tooltip.shelterTip');
      const popup = document.querySelector('.gridTempTooltip');
      if (!label) return;
      if (!popup) {
        label.classList.remove('shelterTipBelow');
        return;
      }

      // 판단 기준은 라벨의 '원래 자리'(핀 위)다. 지금 내려가 있으면 내린 만큼
      // 되돌려서 잰다. 핀 좌표로 재려 했더니 핀과 라벨의 기준 원점이 달라
      // 어긋났고, 현재 자리로 재면 내렸다 올렸다 진동한다.
      const shifted = label.classList.contains('shelterTipBelow');
      const box = label.getBoundingClientRect();
      const top = shifted ? box.top - SHELTER_LABEL_SHIFT : box.top;
      const bottom = shifted ? box.bottom - SHELTER_LABEL_SHIFT : box.bottom;
      const popupBox = popup.getBoundingClientRect();

      label.classList.toggle(
        'shelterTipBelow',
        !(
          box.right < popupBox.left ||
          box.left > popupBox.right ||
          bottom < popupBox.top ||
          top > popupBox.bottom
        )
      );
    };

    // 언제 다시 재는가 — 여기가 핵심이다.
    //
    // `move`·`zoom` 은 애니메이션 도중에 계속 발생한다. 그때는 라벨과 팝업이
    // 아직 옮겨지는 중이라 위치가 틀리고(줌 중에는 pane 에 scale 까지 걸린다),
    // 그 값으로 판단하면 확대·축소할 때마다 겹쳤다 안 겹쳤다 한다.
    // 끝난 뒤(`moveend`·`zoomend`)에만 재고, 그마저도 자리가 완전히 잡히도록
    // 한 박자 늦춰 한 번 더 잰다. 격자 팝업은 이동이 끝난 뒤에 붙으므로
    // `popupopen` 도 듣는다.
    let timers: number[] = [];
    const schedule = () => {
      timers.forEach(window.clearTimeout);
      timers = [window.setTimeout(update, 0), window.setTimeout(update, 220)];
    };

    schedule();
    map.on('moveend zoomend resize popupopen popupclose', schedule);
    return () => {
      timers.forEach(window.clearTimeout);
      map.off('moveend zoomend resize popupopen popupclose', schedule);
    };
  }, [map, shelter, shelterKey]);

  if (!shelter) return null;

  // 핀은 기본 마커 pane(600)에 둔다. 팝업(700) 위로 올렸더니 핀이 격자 코드를
  // 가렸다. 핀은 실제 쉼터 좌표라 피할 수가 없으므로 겹치면 팝업이 이기게 두고,
  // 아래로 삐져나온 부분만 보이게 한다. 라벨은 아래 Tooltip 이 팝업을 피해
  // 내려가므로 온전히 보인다.
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
          항상 띄워두면 hover도 클릭도 필요 없어 둘 다 우회한다.

          '아래로 내리기'는 위 useEffect 가 DOM 클래스로 직접 건다. React 로 하면
          두 가지가 걸린다 — className 은 Leaflet 툴팁을 만들 때 한 번만 반영되고,
          key 로 다시 만들면 위치 계산이 어긋나 핀에서 350px 떨어진 곳에 뜬다(실측). */}
      {/* 전용 pane 을 만들어 팝업 위로 올리지 않는다. 자리를 피하는 것으로
          대부분 해결되고, 그래도 겹치는 드문 경우에는 격자 설명이 이겨야 한다
          — Leaflet 기본값이 툴팁(650) < 팝업(700)이라 그대로 두면 된다. */}
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

function PolicyScopeLayer({ data }: { data: FeatureCollection | null }) {
  const map = useMap();

  useEffect(() => {
    if (!data || data.features.length === 0) return;

    let pane = map.getPane('gridPolicyScopePane');
    if (!pane) pane = map.createPane('gridPolicyScopePane');
    // 100m canvas(350) 위, 기존 단일 선택 outline(450) 아래에 정책 범위만 표시한다.
    pane.style.zIndex = '425';
    pane.style.pointerEvents = 'none';

    const layer = L.geoJSON(data, {
      pane: 'gridPolicyScopePane',
      interactive: false,
      style: {
        color: '#1f5121',
        weight: 2.5,
        opacity: 1,
        dashArray: '6 3',
        fill: false
      }
    }).addTo(map);

    return () => {
      layer.removeFrom(map);
    };
  }, [data, map]);

  return null;
}

function initialDetailPanelOpen() {
  return typeof window === 'undefined' || !window.matchMedia(MOBILE_LAYOUT_QUERY).matches;
}

export function MapDashboard() {
  const [geoJson, setGeoJson] = useState<FeatureCollection | null>(null);
  const [gridGeoJson, setGridGeoJson] = useState<FeatureCollection | null>(null);
  const [selectedDistrict, setSelectedDistrict] = useState(DEFAULT_DISTRICT);
  const [selectedLayer, setSelectedLayer] = useState<LayerKey>(DEFAULT_LAYER);
  const [is3DOpen, setIs3DOpen] = useState(false);
  const previous2DLayerRef = useRef<LayerKey | null>(null);
  const handle3DOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (nextOpen === is3DOpen) return;

      if (nextOpen) {
        previous2DLayerRef.current = selectedLayer;
        setSelectedLayer(LST_LAYER);
      } else {
        setSelectedLayer(previous2DLayerRef.current ?? DEFAULT_LAYER);
        previous2DLayerRef.current = null;
      }
      setIs3DOpen(nextOpen);
    },
    [is3DOpen, selectedLayer]
  );
  // 정책 결과가 지도 전체를 덮고 있는 동안은 지표를 못 바꾸게 잠근다(3D와 같은 규칙).
  // 잠금 여부는 activePolicyBatchResult에서 파생되며, 아래에서 ref에 담아 둔다.
  const isPolicyLayerLockedRef = useRef(false);
  const handleLayerChange = useCallback(
    (layer: LayerKey) => {
      if ((is3DOpen || isPolicyLayerLockedRef.current) && layer !== LST_LAYER) return;
      setSelectedLayer(layer);
    },
    [is3DOpen]
  );
  const [selectedGridResolution, setSelectedGridResolution] =
    useState<GridResolution>(DEFAULT_GRID_RESOLUTION);
  const selectedGridResolutionRef = useRef(selectedGridResolution);
  selectedGridResolutionRef.current = selectedGridResolution;
  const [selectedGridProperties, setSelectedGridProperties] =
    useState<GridAnalysisProperties | null>(null);
  // 정책 결과의 단일 source of truth. 2D/3D는 이 결과에서 표현 값만 파생한다.
  const [batchSimulationResult, setBatchSimulationResult] =
    useState<BatchSimulationResponse | null>(null);
  // 현재/정책 적용 후 보기. 2D·3D가 같은 상태를 쓰므로 모드를 오가도 보던 쪽이 유지된다.
  const [policyViewMode, setPolicyViewMode] = useState<Map3DViewMode>('before');
  const [batchSimulationContext, setBatchSimulationContext] =
    useState<PolicyBatchContext | null>(null);
  // 선택 격자가 속한 '구'의 전체 격자 수 (priority_rank의 분모). 100m 상세 로딩 때 채운다.
  const [selectedGridGuTotal, setSelectedGridGuTotal] = useState<number | null>(null);
  const [selected100mFeature, setSelected100mFeature] =
    useState<Feature<Geometry> | null>(null);
  const [selected100mPopupPosition, setSelected100mPopupPosition] =
    useState<L.LatLng | null>(null);
  const [selectedAggregateFeature, setSelectedAggregateFeature] =
    useState<Feature<Geometry> | null>(null);
  const [selectedAggregatePopupPosition, setSelectedAggregatePopupPosition] =
    useState<L.LatLng | null>(null);
  const [isPanelOpen, setIsPanelOpen] = useState(initialDetailPanelOpen);
  const [activeTool, setActiveTool] = useState('지도선택');
  // 첫 접속이면 사용 가이드를 띄운다. 판단은 마운트 때 한 번만 —
  // 렌더 중에 localStorage를 읽으면 닫은 뒤에도 다시 뜬다.
  const [guideOpen, setGuideOpen] = useState(false);
  useEffect(() => {
    if (shouldShowOnboarding()) setGuideOpen(true);
  }, []);
  const activeToolRef = useRef(activeTool);
  activeToolRef.current = activeTool;   // 항상 최신 모드를 담아둠 (클릭 핸들러가 참조)
  const selectedGridIdRef = useRef<string | null>(null);   // 현재 팝업이 열린 격자 id
  const selectedGridLayerRef = useRef<L.Path | null>(null);   // 현재 선택 격자의 Leaflet 레이어
  const selectedResetRef = useRef<(() => void) | null>(null);   // 선택 격자 테두리 원복 함수
  const seoul100mMapCacheRef = useRef<FeatureCollection | null>(null);
  const district100mCacheRef = useRef(new Map<string, FeatureCollection>());
  // 같은 구의 상세 파일을 중복 요청하지 않도록 진행 중인 Promise도 공유한다.
  const district100mRequestRef = useRef(new Map<string, Promise<FeatureCollection>>());
  const districtAggregateCacheRef = useRef(new Map<string, FeatureCollection>());
  const districtAggregateRequestRef = useRef(
    new Map<string, Promise<FeatureCollection>>()
  );
  const gridGeoJsonRef = useRef(gridGeoJson);
  gridGeoJsonRef.current = gridGeoJson;
  const dongSearchRequestRef = useRef(0);
  const selectedDistrictRef = useRef(selectedDistrict);
  selectedDistrictRef.current = selectedDistrict;
  const handleBatchSimulationResult = useCallback(
    (result: BatchSimulationResponse | null) => {
      setBatchSimulationResult(result);
      setBatchSimulationContext(
        result
          ? {
              resolution: selectedGridResolutionRef.current,
              district: selectedDistrictRef.current,
              selectedGridId: selectedGridIdRef.current
            }
          : null
      );
    },
    []
  );

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
  const [dongSearchEntries, setDongSearchEntries] = useState<DongSearchIndexEntry[]>([]);
  const [dongSearchLoading, setDongSearchLoading] = useState(true);
  const [dongSearchError, setDongSearchError] = useState<string | null>(null);

  // 모바일로 진입하거나 데스크톱 창을 모바일 폭으로 줄이면 지도부터 보이게 상세 패널을 닫는다.
  useEffect(() => {
    const media = window.matchMedia(MOBILE_LAYOUT_QUERY);
    const handleChange = (event: MediaQueryListEvent) => {
      if (event.matches) setIsPanelOpen(false);
    };
    media.addEventListener('change', handleChange);
    return () => media.removeEventListener('change', handleChange);
  }, []);

  useEffect(() => {
    getGeoJson()
      .then((geoJsonData) => setGeoJson(geoJsonData))
      .catch((requestError: unknown) => {
        setError(requestError instanceof Error ? requestError.message : '데이터 요청 실패');
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    let active = true;
    getDongSearchIndex()
      .then((index) => {
        if (active) setDongSearchEntries(index.dongs);
      })
      .catch(() => {
        if (active) setDongSearchError('행정동 검색 데이터를 불러오지 못했습니다.');
      })
      .finally(() => {
        if (active) setDongSearchLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const districts = useMemo(() => buildDistrictOptions(geoJson), [geoJson]);
  const isAllDistricts = selectedDistrict === ALL_DISTRICTS;
  const selectedDistrictMeta = districts.find((district) => district.district === selectedDistrict);
  const districtCodeByName = useMemo(
    () => new Map(districts.map((district) => [district.district, district.sigCode])),
    [districts]
  );
  // 격자 코드 앞 5자리로 어느 구인지 되찾기 위한 역방향 표.
  const districtNameByCode = useMemo(
    () => new Map(districts.map((district) => [district.sigCode, district.district])),
    [districts]
  );
  // ── 격자 코드 검색 ────────────────────────────────────────────────────────
  // 격자 수가 많아 특정 코드를 지도에서 눈으로 찾기 어렵다. 검색 대상 해상도는
  // 코드 형식으로 추측하지 않고 사용자가 현재 선택한 해상도만 사용한다.
  const [gridSearchError, setGridSearchError] = useState<string | null>(null);
  const [gridSearchBusy, setGridSearchBusy] = useState(false);
  // 찾은 격자를 곧바로 반영하지 않고 상태에 담아 두는 이유는 위 '검색 결과 반영' effect 주석 참고.
  const [gridSearchHit, setGridSearchHit] = useState<Feature<Geometry> | null>(null);
  const [aggregateGridSearchHit, setAggregateGridSearchHit] =
    useState<AggregateGridSearchHit | null>(null);
  // 검색으로 이동할 때만 구 중심 대신 격자 중심을 본다. null이면 평소대로 구 중심.
  const [gridFocus, setGridFocus] = useState<{ center: [number, number]; zoom: number } | null>(
    null
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

  const loadDistrictAggregateGrid = useCallback(
    (resolution: AggregateGridResolution, sigCode: string, districtName: string) => {
      const cacheKey = `${resolution}:${sigCode}`;
      const cached = districtAggregateCacheRef.current.get(cacheKey);
      if (cached) return Promise.resolve(cached);

      const pending = districtAggregateRequestRef.current.get(cacheKey);
      if (pending) return pending;

      const request = getDistrictResolutionGrid(resolution, sigCode, districtName)
        .then((collection) => {
          const districtCollection =
            resolution === '500m' ? filterGridByDistrict(collection, sigCode) : collection;
          districtAggregateCacheRef.current.set(cacheKey, districtCollection);
          return districtCollection;
        })
        .finally(() => {
          districtAggregateRequestRef.current.delete(cacheKey);
        });

      districtAggregateRequestRef.current.set(cacheKey, request);
      return request;
    },
    []
  );

  const handleGridSearch = useCallback(
    async (
      rawCode: string,
      expectedDong?: Pick<DongSearchIndexEntry, 'sig_code' | 'dong_name'>
    ) => {
      const code = rawCode.trim();
      const resolution = selectedGridResolutionRef.current;
      const requestId = ++dongSearchRequestRef.current;
      setGridSearchError(null);
      setGridSearchHit(null);
      setAggregateGridSearchHit(null);

      const matched = GRID_CODE_PATTERN.exec(code);
      if (!matched) {
        setGridSearchError('격자 코드는 11560_02332 처럼 다섯 자리_다섯 자리예요.');
        return false;
      }

      if (resolution === 'gu') {
        setGridSearchError('구 단위에서는 격자 코드 검색을 사용할 수 없습니다.');
        return false;
      }

      const sigCode = matched[1];
      if (expectedDong && sigCode !== expectedDong.sig_code) return false;

      // 앞 다섯 자리는 해상도 판별에 쓰지 않고, 실제 자치구 경계 데이터에서 만든
      // 역방향 표로 해당 해상도의 자치구 파일 하나를 찾는 데만 사용한다.
      const districtName = districtNameByCode.get(sigCode);
      if (!districtName) {
        setGridSearchError(
          `현재 ${resolution.toUpperCase()} 격자에서 해당 코드를 찾을 수 없습니다.`
        );
        return false;
      }

      setGridSearchBusy(true);
      try {
        // 먼저 현재 해상도의 실제 데이터를 확인한 뒤 성공할 때만 화면 상태를 바꾼다.
        // 따라서 실패해도 기존 선택과 해상도는 그대로 유지된다.
        const collection =
          resolution === '100m'
            ? await loadDistrict100mDetails(sigCode, districtName)
            : await loadDistrictAggregateGrid(resolution, sigCode, districtName);

        if (
          dongSearchRequestRef.current !== requestId ||
          selectedGridResolutionRef.current !== resolution
        ) {
          return false;
        }

        const feature = collection.features.find((candidate) => {
          const typedCandidate = candidate as Feature<Geometry>;
          const properties = getFeatureProperties(typedCandidate);
          const identifier =
            resolution === '100m' ? properties.grid_id : properties.display_grid_id;
          return identifier === code && getFeatureGuCode(typedCandidate) === sigCode;
        }) as Feature<Geometry> | undefined;

        if (!feature) {
          setGridSearchError(
            `현재 ${resolution.toUpperCase()} 격자에서 해당 코드를 찾을 수 없습니다.`
          );
          return false;
        }

        const properties = getFeatureProperties(feature);
        if (
          expectedDong &&
          (String(properties.gu_code ?? '').trim() !== expectedDong.sig_code ||
            String(properties.dong_name ?? '').trim() !== expectedDong.dong_name)
        ) {
          return false;
        }

        const center = L.geoJSON(feature).getBounds().getCenter();
        setSelectedDistrict(districtName);
        if (resolution === '100m') {
          setGridSearchHit(feature);
          setGridFocus({ center: [center.lat, center.lng], zoom: GRID_SEARCH_ZOOM });
        } else {
          setGridGeoJson(collection);
          setAggregateGridSearchHit({ feature, resolution, district: districtName });
          setGridFocus({
            center: [center.lat, center.lng],
            zoom: getGridSearchZoom(resolution)
          });
        }
        return true;
      } catch {
        setGridSearchError('격자 데이터를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.');
        return false;
      } finally {
        setGridSearchBusy(false);
      }
    },
    [districtNameByCode, loadDistrict100mDetails, loadDistrictAggregateGrid]
  );

  // 사용자가 직접 구·해상도를 바꾸면 검색 이동을 풀어 평소의 구 중심 보기로 돌아간다.
  const handleDistrictChange = useCallback((district: string) => {
    dongSearchRequestRef.current += 1;
    setGridFocus(null);
    setGridSearchError(null);
    setAggregateGridSearchHit(null);
    handleBatchSimulationResult(null);
    // 3D를 연 채 지역을 바꿀 때 이전 지역 source가 새 context로 잠시 보이지 않게 비운다.
    setGridGeoJson(null);
    setSelectedDistrict(district);
  }, [handleBatchSimulationResult]);
  const handleDongSearch = useCallback(
    async (entry: DongSearchIndexEntry) => {
      const resolution = selectedGridResolution;
      if (resolution === '100m') {
        const centerGridId = entry.center_grid_id?.trim();
        if (centerGridId) {
          const selected = await handleGridSearch(centerGridId, entry);
          if (selected) return;
        }
      } else if (resolution === '250m' || resolution === '500m') {
        const requestId = ++dongSearchRequestRef.current;
        setGridSearchError(null);
        setGridSearchBusy(true);
        try {
          const collection = await loadDistrictAggregateGrid(
            resolution,
            entry.sig_code,
            entry.district
          );
          if (
            dongSearchRequestRef.current !== requestId ||
            selectedGridResolutionRef.current !== resolution
          ) {
            return;
          }

          const districtFeatures = collection.features.filter(
            (feature) => getFeatureGuCode(feature as Feature<Geometry>) === entry.sig_code
          ) as Feature<Geometry>[];
          const feature = findDongCenterGridFeature(districtFeatures, entry.center);
          if (feature) {
            const center = L.geoJSON(feature).getBounds().getCenter();
            setSelectedDistrict(entry.district);
            setGridGeoJson(collection);
            setAggregateGridSearchHit({ feature, resolution, district: entry.district });
            setGridFocus({
              center: [center.lat, center.lng],
              zoom: getGridSearchZoom(resolution)
            });
            return;
          }
        } catch {
          // 기존 행정동 중심 이동 fallback은 아래에서 처리한다.
        } finally {
          setGridSearchBusy(false);
        }
      }

      // 대표 격자를 찾지 못하면 resolution은 유지하고 기존 행정동 중심 이동으로 복귀한다.
      handleDistrictChange(entry.district);
      setGridFocus({ center: entry.center, zoom: DONG_SEARCH_FALLBACK_ZOOM });
    },
    [
      handleDistrictChange,
      handleGridSearch,
      loadDistrictAggregateGrid,
      selectedGridResolution
    ]
  );
  const handleGridResolutionChange = useCallback((resolution: GridResolution) => {
    dongSearchRequestRef.current += 1;
    setGridFocus(null);
    setGridSearchError(null);
    setAggregateGridSearchHit(null);
    handleBatchSimulationResult(null);
    // MapLibre instance는 유지하되 새 해상도 데이터가 올 때까지 기존 source를 비운다.
    setGridGeoJson(null);
    setSelectedGridResolution(resolution);
  }, [handleBatchSimulationResult]);

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
    setSelectedAggregateFeature(null);
    setSelectedAggregatePopupPosition(null);
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
    setSelectedAggregateFeature(null);
    setSelectedAggregatePopupPosition(null);
    setSelectedGridProperties(null);

    // 구 모드는 격자를 쓰지 않는다. 남아 있던 격자를 비워야 지도에 겹쳐 그려지지 않는다.
    if (isGuResolution(selectedGridResolution)) {
      setGridGeoJson(null);
      setGridLoading(false);
      return;
    }

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

    loadDistrictAggregateGrid(
      selectedGridResolution as AggregateGridResolution,
      sigCode,
      selectedDistrict
    )
      .then((data) => {
        if (isActive) {
          setGridGeoJson(data);
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
    loadDistrictAggregateGrid,
    selectedDistrict,
    selectedGridResolution
  ]);

  // ★ 반드시 위 '구 전환' useEffect 뒤에 선언한다. 위 effect가 구를 바꿀 때마다
  //   selectedGridProperties를 null로 지우므로, 클릭 핸들러에서 값을 넣으면
  //   곧바로 덮어써진다(실제로 그래서 지표 목록이 계속 '선택 전'이었다).
  //   상태를 직접 넣는 대신 선택된 구에서 파생시켜 순서 문제를 없앤다.
  useEffect(() => {
    if (!isGuResolution(selectedGridResolution)) return;
    if (!geoJson || selectedDistrict === ALL_DISTRICTS) return;

    const feature = geoJson.features.find(
      (candidate) => getDistrictName(candidate as Feature<Geometry>) === selectedDistrict
    );
    if (feature) {
      setSelectedGridProperties(getFeatureProperties(feature as Feature<Geometry>));
    }
  }, [geoJson, selectedDistrict, selectedGridResolution]);

  const center = selectedDistrictMeta?.center ?? DEFAULT_CENTER;
  const targetZoom = isAllDistricts ? SEOUL_OVERVIEW_ZOOM : DISTRICT_DETAIL_ZOOM;
  const isDistrictOverview = isAllDistricts || zoomLevel <= DISTRICT_OVERVIEW_ZOOM;

  // ★ 구 경계를 '칠하는' 판단은 줌이 아니라 해상도 선택으로만 한다.
  //   예전에는 isDistrictOverview(전체 선택 또는 낮은 줌)로 결정해서 격자와 동시에
  //   그려졌고, 화면에 보이는 색이 격자인지 구인지 구분할 수 없었다.
  const isGuMode = isGuResolution(selectedGridResolution);

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
  const selectedAggregateGridId = selectedAggregateFeature
    ? String(getFeatureProperties(selectedAggregateFeature).display_grid_id ?? '').trim() || null
    : null;
  const selectedMapGridId =
    selectedGridResolution === '100m' ? selected100mGridId : selectedAggregateGridId;
  const compareGridId = comparePropertiesB ? getGridIdentifier(comparePropertiesB) : null;
  const handle100mFeatureClick = useCallback(
    (feature: Feature<Geometry>, latLng: L.LatLng, selectAsPrimary = false) => {
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
      if (isPickingCompareRef.current && !selectAsPrimary) {
        if (gridId === selectedGridIdRef.current) return;   // 같은 격자끼리 비교 불가
        compareGridIdRef.current = gridId;
        setComparePropertiesB(fullProperties ?? properties);
        setIsPickingCompare(false);
        if (!fullProperties) hydrateCompareProperties(properties);
        return;
      }

      if (selectAsPrimary) setIsPickingCompare(false);

      selectedResetRef.current?.();
      handleBatchSimulationResult(null);
      selectedGridIdRef.current = gridId;
      selectedGridLayerRef.current = null;
      setSelected100mFeature(feature);
      setSelected100mPopupPosition(latLng);
      setSelectedAggregateFeature(null);
      setSelectedAggregatePopupPosition(null);
      // 상세 캐시가 있으면 경량 속성을 거치지 않고 완성된 속성을 한 번에 반영한다.
      setSelectedGridProperties(fullProperties ?? properties);
      setSelectedGridGuTotal(cached?.features.length ?? null);
      if (!fullProperties) hydrateSelectedGridProperties(properties);
    },
    [
      districtCodeByName,
      handleBatchSimulationResult,
      hydrateSelectedGridProperties,
      hydrateCompareProperties
    ]
  );
  const handleAggregateFeatureClick = useCallback(
    (feature: Feature<Geometry>, latLng: L.LatLng) => {
      const properties = getFeatureProperties(feature);
      const gridId =
        typeof properties.display_grid_id === 'string'
          ? properties.display_grid_id.trim()
          : '';
      if (!gridId) return;

      // 비교 선택 의미는 2D와 동일하게 유지하되, 3D에는 별도 B highlight를 만들지 않는다.
      if (isPickingCompareRef.current) {
        if (gridId === selectedGridIdRef.current) return;
        compareResetRef.current?.();
        compareGridIdRef.current = gridId;
        setComparePropertiesB(properties);
        setIsPickingCompare(false);
        return;
      }

      selectedResetRef.current?.();
      handleBatchSimulationResult(null);
      selectedGridIdRef.current = gridId;
      selectedGridLayerRef.current = null;
      setSelected100mFeature(null);
      setSelected100mPopupPosition(null);
      setSelectedAggregateFeature(feature);
      setSelectedAggregatePopupPosition(latLng);
      setSelectedGridProperties(properties);

      const guKey = properties.gu_code ?? properties.gu_name;
      const features = gridGeoJsonRef.current?.features ?? [];
      const total =
        guKey != null
          ? features.filter((candidate) => {
              const candidateProperties = getFeatureProperties(
                candidate as Feature<Geometry>
              );
              return (candidateProperties.gu_code ?? candidateProperties.gu_name) === guKey;
            }).length
          : features.length;
      setSelectedGridGuTotal(total || null);
    },
    [handleBatchSimulationResult]
  );
  const clearSelectedMapGrid = useCallback((gridId: string) => {
    if (selectedGridIdRef.current !== gridId) return;
    selectedResetRef.current?.();
    selectedGridIdRef.current = null;
    setSelected100mFeature(null);
    setSelected100mPopupPosition(null);
    setSelectedAggregateFeature(null);
    setSelectedAggregatePopupPosition(null);
    setSelectedGridProperties(null);
    handleBatchSimulationResult(null);
  }, [handleBatchSimulationResult]);
  const handle3DGridFeatureClick = useCallback(
    (
      feature: Feature<Geometry>,
      position: { lng: number; lat: number }
    ) => {
      if (activeToolRef.current !== '지도선택') return;
      const latLng = L.latLng(position.lat, position.lng);
      if (selectedGridResolutionRef.current === '100m') {
        handle100mFeatureClick(feature, latLng);
      } else {
        handleAggregateFeatureClick(feature, latLng);
      }
    },
    [handle100mFeatureClick, handleAggregateFeatureClick]
  );
  const handle3DDistrictDrillDown = useCallback(
    (guCode: string) => {
      const districtName = districtNameByCode.get(guCode);
      if (districtName) handleDistrictChange(districtName);
    },
    [districtNameByCode, handleDistrictChange]
  );
  // 구·해상도 전환 effect가 기존 선택을 비운 뒤 검색 결과를 실제 100m 클릭과 같은 경로로 반영한다.
  useEffect(() => {
    if (!gridSearchHit) return;
    const center = L.geoJSON(gridSearchHit).getBounds().getCenter();
    handle100mFeatureClick(gridSearchHit, center, true);
    setGridSearchHit(null);
  }, [gridSearchHit, handle100mFeatureClick]);
  const build100mTooltip = useCallback(
    (feature: Feature<Geometry>) => buildGridTooltip(feature, selectedLayer, 100),
    [selectedLayer]
  );
  const buildMapGridTooltip = useCallback(
    (feature: Feature<Geometry>) =>
      buildGridTooltip(feature, selectedLayer, getResolutionMeters(selectedGridResolution)),
    [selectedGridResolution, selectedLayer]
  );
  const mapGridInfoSign = useMemo(
    () => {
      const selectedFeature =
        selectedGridResolution === '100m'
          ? selected100mFeature
          : selectedAggregateFeature;
      const popupPosition =
        selectedGridResolution === '100m'
          ? selected100mPopupPosition
          : selectedAggregatePopupPosition;
      return selectedFeature && popupPosition && selectedMapGridId
        ? {
            gridId: selectedMapGridId,
            position: {
              lng: popupPosition.lng,
              lat: popupPosition.lat
            },
            html: buildMapGridTooltip(selectedFeature)
          }
        : null;
    },
    [
      buildMapGridTooltip,
      selectedAggregateFeature,
      selectedAggregatePopupPosition,
      selectedGridResolution,
      selectedMapGridId,
      selected100mFeature,
      selected100mPopupPosition
    ]
  );
  const mapShelterSign = useMemo(
    () => getShelterPin(selectedGridProperties),
    [selectedGridProperties]
  );
  const selectedDistrictCode = districtCodeByName.get(selectedDistrict) ?? null;
  // 분석 지역이 '전체'여도 격자를 고르면 그 격자의 구를 대상으로 정책을 돌릴 수 있다.
  // 그때는 자치구 코드를 선택 격자에서 가져온다.
  const selectedGridGuCode =
    selectedGridProperties?.gu_code != null
      ? String(selectedGridProperties.gu_code)
      : null;
  const policyDistrictCode = selectedDistrictCode ?? selectedGridGuCode;
  const policyContextMatches =
    batchSimulationContext?.resolution === selectedGridResolution &&
    batchSimulationContext.district === selectedDistrict &&
    batchSimulationContext.selectedGridId === selectedMapGridId;
  const isActiveSeoulPolicyResult =
    selectedDistrict === ALL_DISTRICTS &&
    selectedGridResolution !== 'gu' &&
    batchSimulationResult?.target_mode === 'seoul' &&
    (selectedGridResolution === '100m'
      ? batchSimulationResult.display_resolution === undefined ||
        batchSimulationResult.display_resolution === '100m'
      : batchSimulationResult.display_resolution === selectedGridResolution);
  const isActive100mPolicyResult =
    selectedGridResolution === '100m' &&
    (batchSimulationResult?.target_mode === 'spatial_scope' ||
      (batchSimulationResult?.target_mode === 'district' &&
        policyDistrictCode !== null &&
        batchSimulationResult.gu_code === policyDistrictCode &&
        (batchSimulationResult.display_resolution === undefined ||
          batchSimulationResult.display_resolution === '100m')));
  const isActiveAggregatePolicyResult =
    (selectedGridResolution === '250m' || selectedGridResolution === '500m') &&
    selectedMapGridId !== null &&
    batchSimulationResult?.target_mode === 'aggregate' &&
    batchSimulationResult.aggregate_resolution === selectedGridResolution &&
    batchSimulationResult.aggregate_id === selectedMapGridId;
  const isActiveDistrictDisplayPolicyResult =
    (selectedGridResolution === '250m' || selectedGridResolution === '500m') &&
    batchSimulationResult?.target_mode === 'district' &&
    policyDistrictCode !== null &&
    batchSimulationResult.gu_code === policyDistrictCode &&
    batchSimulationResult.display_resolution === selectedGridResolution;
  const activePolicyBatchResult =
    policyContextMatches &&
    (isActiveSeoulPolicyResult ||
      isActive100mPolicyResult ||
      isActiveAggregatePolicyResult ||
      isActiveDistrictDisplayPolicyResult)
      ? batchSimulationResult
      : null;

  // 새 결과는 바로 '정책 적용 후'로 보여주고, 결과가 풀리면 '현재'로 되돌린다.
  useEffect(() => {
    setPolicyViewMode(activePolicyBatchResult ? 'after' : 'before');
  }, [activePolicyBatchResult]);

  // 정책 결과가 붙으면 지도 지표를 지표면온도로 맞춘다. delta_c는 온도 변화량이라
  // 우선순위·녹지율 위에 덧칠하면 범례와 색이 서로 다른 것을 가리키게 된다.
  // is3DOpen도 본다 — 3D를 닫으면 handle3DOpenChange가 이전 지표를 되돌리는데,
  // 결과가 살아 있는 채로 되돌아가면 2D에서 전후 토글이 색을 못 바꾼다.
  useEffect(() => {
    if (!activePolicyBatchResult) return;
    setSelectedLayer(LST_LAYER);
  }, [activePolicyBatchResult, is3DOpen]);

  // grid_id → delta_c. 2D 채색이 격자마다 찾아 쓰므로 결과당 한 번만 만든다.
  const policyAfterDeltas = useMemo(() => {
    if (!activePolicyBatchResult) return null;
    const deltas = new Map<string, number>();

    // aggregate 결과의 results는 구성 100m 셀이라 지도에 그려지는 250/500m 격자와
    // ID가 맞지 않는다. 3D와 같이 선택한 집계 격자 하나에 평균값을 칠한다.
    if (activePolicyBatchResult.target_mode === 'aggregate') {
      const mean = activePolicyBatchResult.mean_delta_c;
      const aggregateId = activePolicyBatchResult.aggregate_id;
      if (aggregateId && typeof mean === 'number' && Number.isFinite(mean)) {
        deltas.set(aggregateId, mean);
      }
      return deltas.size > 0 ? deltas : null;
    }

    for (const result of activePolicyBatchResult.results) {
      if (result.status !== 'success') continue;
      const delta = result.delta_c;
      if (typeof delta === 'number' && Number.isFinite(delta)) {
        deltas.set(result.grid_id, delta);
      }
    }
    return deltas.size > 0 ? deltas : null;
  }, [activePolicyBatchResult]);
  // 격자를 고르지 않은 채 정책을 돌리면 결과가 지도 전체를 덮는다. 그동안 지표를 바꾸면
  // 범례와 색이 서로 다른 것을 가리키므로 지표 선택을 잠근다.
  // 격자를 클릭하면 결과가 지워지고(selectGridFeatureLayer) 잠금도 함께 풀린다.
  const isPolicyLayerLocked = Boolean(activePolicyBatchResult) && !selectedGridProperties;
  isPolicyLayerLockedRef.current = isPolicyLayerLocked;

  // 격자를 고른 뒤에는 잠기지 않으므로 사용자가 다른 지표로 바꿀 수 있다.
  // 그때는 덧칠을 멈춰야 범례와 색이 어긋나지 않는다.
  const activeAfterDeltas =
    policyViewMode === 'after' && selectedLayer === LST_LAYER ? policyAfterDeltas : null;

  const policyGridIds = useMemo(() => {
    if (activePolicyBatchResult?.target_mode !== 'spatial_scope') return [];
    const ids = activePolicyBatchResult?.results
      .map((result) => result.grid_id.trim())
      .filter(Boolean) ?? [];
    return [...new Set(ids)];
  }, [activePolicyBatchResult]);
  const policyScopeGridData = useMemo<FeatureCollection | null>(() => {
    if (policyGridIds.length === 0) return null;

    const targetIds = new Set(policyGridIds);
    const targetFeatures: FeatureCollection['features'] = [];
    for (const feature of seoul100mMapCacheRef.current?.features ?? []) {
      const gridId = getGridIdentifier(
        getFeatureProperties(feature as Feature<Geometry>)
      );
      if (!targetIds.has(gridId)) continue;
      targetFeatures.push(feature);
      if (targetFeatures.length === targetIds.size) break;
    }

    return {
      type: 'FeatureCollection',
      features: targetFeatures
    };
  }, [policyGridIds]);
  const map3DGridData = useMemo<FeatureCollection | null>(() => {
    if (!gridGeoJson || isGuResolution(selectedGridResolution)) {
      return null;
    }
    // 250m/500m는 기존 2D가 로드한 실제 aggregate collection을 그대로 시각화한다.
    if (selectedGridResolution !== '100m') return gridGeoJson;
    if (selectedDistrict === ALL_DISTRICTS) return gridGeoJson;
    if (!policyScopeGridData) return gridGeoJson;

    const currentGridIds = new Set(
      gridGeoJson.features.map((feature) =>
        getGridIdentifier(getFeatureProperties(feature as Feature<Geometry>))
      )
    );
    const missingTargetFeatures = policyScopeGridData.features.filter((feature) => {
      const gridId = getGridIdentifier(
        getFeatureProperties(feature as Feature<Geometry>)
      );
      return !currentGridIds.has(gridId);
    });
    if (missingTargetFeatures.length === 0) return gridGeoJson;

    // 선택 자치구 + 경계 밖 정책 대상만 새 collection으로 합친다.
    // 원본 cache/gridGeoJson은 mutate하지 않고, 서울 전체를 MapLibre에 넘기지 않는다.
    return {
      ...gridGeoJson,
      features: [...gridGeoJson.features, ...missingTargetFeatures]
    };
  }, [gridGeoJson, policyScopeGridData, selectedDistrict, selectedGridResolution]);
  const selectedLayerKeyRef = useRef(selectedLayer);
  const isDistrictOverviewRef = useRef(isDistrictOverview);
  const hydrateSelectedGridPropertiesRef = useRef(hydrateSelectedGridProperties);
  selectedLayerKeyRef.current = selectedLayer;
  isDistrictOverviewRef.current = isDistrictOverview;
  hydrateSelectedGridPropertiesRef.current = hydrateSelectedGridProperties;
  const resolutionGridLayerRef = useRef<L.GeoJSON | null>(null);

  const selectGridFeatureLayer = useCallback(
    (layer: GridFeatureLayer, feature: Feature<Geometry>, selectAsPrimary = false) => {
      const props = getFeatureProperties(feature);
      const gridId = getGridIdentifier(props);
      const resolution = selectedGridResolutionRef.current;
      const meters = getResolutionMeters(resolution);

      // 비교 픽 모드: B에 담고 A(주 격자)는 그대로 둔다.
      if (isPickingCompareRef.current && !selectAsPrimary) {
        if (gridId === selectedGridIdRef.current) return;
        compareResetRef.current?.();
        layer.setStyle({ color: '#1c7ed6', weight: 3, opacity: 1 });
        compareGridLayerRef.current = layer;
        compareGridIdRef.current = gridId;
        setComparePropertiesB(props);
        setIsPickingCompare(false);
        compareResetRef.current = () => {
          compareGridLayerRef.current = null;
          compareResetRef.current = null;
          layer.setStyle(gridStyleRef.current(feature));
        };
        return;
      }

      if (selectAsPrimary) setIsPickingCompare(false);

      selectedResetRef.current?.();
      handleBatchSimulationResult(null);
      layer.setStyle({ color: '#111827', weight: 3, opacity: 1 });
      selectedGridLayerRef.current = layer;
      setSelectedGridProperties(props);
      setSelectedGridGuTotal(null);
      selectedGridIdRef.current = gridId;
      if (resolution === '100m') {
        setSelectedAggregateFeature(null);
        setSelectedAggregatePopupPosition(null);
        hydrateSelectedGridPropertiesRef.current(props);
      } else {
        const popupPosition = L.geoJSON(feature).getBounds().getCenter();
        setSelected100mFeature(null);
        setSelected100mPopupPosition(null);
        setSelectedAggregateFeature(feature);
        setSelectedAggregatePopupPosition(popupPosition);
        // 250/500m: priority_rank는 구 내부 순위 → 같은 구의 그 해상도 격자 수가 분모
        const guKey = props.gu_code ?? props.gu_name;
        const feats = gridGeoJsonRef.current?.features ?? [];
        const total =
          guKey != null
            ? feats.filter((candidate) => {
                const candidateProperties = getFeatureProperties(
                  candidate as Feature<Geometry>
                );
                return (candidateProperties.gu_code ?? candidateProperties.gu_name) === guKey;
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
        layer.setStyle(gridStyleRef.current(feature));
        layer.closePopup();
        layer.unbindPopup();
        setSelectedAggregateFeature(null);
        setSelectedAggregatePopupPosition(null);
        setSelectedGridProperties(null);
        handleBatchSimulationResult(null);
      };

      layer.once('popupclose', () => {
        if (selectedGridIdRef.current !== gridId) return;
        selectedGridIdRef.current = null;
        selectedGridLayerRef.current = null;
        selectedResetRef.current = null;
        layer.setStyle(gridStyleRef.current(feature));
        layer.unbindPopup();
        setSelectedAggregateFeature(null);
        setSelectedAggregatePopupPosition(null);
        setSelectedGridProperties(null);
        handleBatchSimulationResult(null);
      });
    },
    [handleBatchSimulationResult]
  );

  const handleGridClick = useCallback(
    (event: L.LeafletMouseEvent) => {
      if (activeToolRef.current !== '지도선택') return;
      if (event.originalEvent && event.originalEvent.button !== 0) return;
      const layer = (event.target ?? event.sourceTarget) as GridFeatureLayer;
      if (!layer.feature) return;
      selectGridFeatureLayer(layer, layer.feature);
    },
    [selectGridFeatureLayer]
  );

  useEffect(() => {
    if (!aggregateGridSearchHit) return;
    if (
      selectedGridResolution !== aggregateGridSearchHit.resolution ||
      selectedDistrict !== aggregateGridSearchHit.district
    ) {
      return;
    }

    const targetGridId = getFeatureProperties(
      aggregateGridSearchHit.feature
    ).display_grid_id;
    let targetLayer: GridFeatureLayer | null = null;
    resolutionGridLayerRef.current?.eachLayer((candidate) => {
      const layer = candidate as GridFeatureLayer;
      if (
        layer.feature &&
        getFeatureProperties(layer.feature).display_grid_id === targetGridId
      ) {
        targetLayer = layer;
      }
    });

    if (!targetLayer) return;
    selectGridFeatureLayer(targetLayer, aggregateGridSearchHit.feature, true);
    setAggregateGridSearchHit(null);
  }, [
    aggregateGridSearchHit,
    selectGridFeatureLayer,
    selectedDistrict,
    selectedGridResolution
  ]);

  // style 함수를 고정(useCallback)해야 리렌더 때 react-leaflet이 전체를 다시 칠하지 않음
  // (안 그러면 선택 격자에 준 테두리가 리렌더마다 지워짐)
  const gridStyle = useCallback(
    (feature?: Feature<Geometry>) =>
      gridFeatureStyle(
        feature as Feature<Geometry>,
        selectedLayer,
        isDistrictOverview,
        activeAfterDeltas
      ),
    [selectedLayer, isDistrictOverview, activeAfterDeltas]
  );
  const gridStyleRef = useRef(gridStyle);
  gridStyleRef.current = gridStyle;

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
          /* preferCanvas 를 켜지 않는다.
             켜면 Leaflet이 <GeoJSON> 같은 벡터 레이어를 공용 캔버스 렌더러로 그리는데,
             이 앱에서는 그 렌더러가 지도에 붙지 않아 구 경계·250m/500m 격자가
             통째로 안 그려졌다(캔버스 draw 호출 0회. 레이어와 style 함수는 정상 동작해서
             로그만으로는 정상으로 보였다).
             64,672개짜리 100m 격자는 CanvasGridLayer가 따로 캔버스로 그리므로
             preferCanvas 없이도 성능 문제가 없다. */
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <MapZoomWatcher onZoomChange={setZoomLevel} />
          <MapFocus
            center={gridFocus?.center ?? center}
            zoom={gridFocus?.zoom ?? targetZoom}
          />
          <RightDragPan />
          <GeoJSON
            key={`${selectedDistrict}-${selectedLayer}-${isGuMode}`}
            data={geoJson}
            interactive={isGuMode}
            style={(feature) =>
              getBoundaryStyle(
                feature as Feature<Geometry>,
                selectedDistrict,
                selectedLayer,
                isGuMode,
                districtQuantiles
              )
            }
            onEachFeature={(feature, layer) => {
              const typedFeature = feature as Feature<Geometry>;
              const districtName = getDistrictName(typedFeature);

              if (isGuMode) {
                layer.bindTooltip(buildDistrictTooltip(typedFeature, selectedLayer), {
                  className: 'districtTempTooltip',
                  direction: 'top',
                  opacity: 1,
                  sticky: true
                });
              }

              layer.on({
                click: () => handleDistrictChange(districtName),
                mouseover: () => {
                  if (isGuMode) {
                    (layer as L.Path).setStyle({
                      fillOpacity: 0.9,
                      opacity: 1,
                      weight: 3.4
                    });
                  }
                },
                mouseout: () => {
                  if (isGuMode) {
                    (layer as L.Path).setStyle(
                      getBoundaryStyle(
                        typedFeature,
                        selectedDistrict,
                        selectedLayer,
                        isGuMode,
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
          {selectedGridResolution === '100m' && (
            <PolicyScopeLayer data={policyScopeGridData} />
          )}
          {gridGeoJson && selectedGridResolution !== '100m' && !isGuMode && (
            <GeoJSON
              ref={resolutionGridLayerRef}
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
                    (layer as L.Path).setStyle(gridStyleRef.current(layer.feature));
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
                  remove: () => clearSelectedMapGrid(selected100mGridId)
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
              eventHandlers={{ click: () => handleDistrictChange(district.district) }}
            />
          ))}
          {/* 선택 격자의 가장 가까운 쉼터를 지도에 핀으로 표시 (100m는 hydrate 후 좌표가 채워짐) */}
          <ShelterMarker shelter={getShelterPin(selectedGridProperties)} />
          {/* 줌·축척을 우측 상단 도구 팔레트(.rightToolbar) 옆으로 모았다.
              지도 조작 컨트롤이 화면 양 끝에 흩어져 있으면 시선이 두 번 움직인다. */}
          <ZoomControl position="topright" />
          {/* 카드 안쪽 폭이 50px이라 maxWidth를 40으로 둔다.
              50으로 맞추면 막대가 좌우 여백 없이 꽉 차서 넘친 것처럼 보인다. */}
          <ScaleControl position="topright" metric imperial={false} maxWidth={40} />
        </MapContainer>
      )}
      <Map3DOverlay
        is3DOpen={is3DOpen}
        on3DOpenChange={handle3DOpenChange}
        gridData={map3DGridData}
        batchSimulationResult={activePolicyBatchResult}
        viewMode={policyViewMode}
        onViewModeChange={setPolicyViewMode}
        resolution={selectedGridResolution}
        selectedDistrict={selectedDistrict}
        selectedGridId={selectedMapGridId}
        gridInfoSign={mapGridInfoSign}
        shelterSign={mapShelterSign}
        onGridFeatureClick={handle3DGridFeatureClick}
        onDistrictDrillDown={handle3DDistrictDrillDown}
        onClearSelectedGrid={clearSelectedMapGrid}
      />
      {!loading && !error && gridLoading && (
        <div className="mapLoadingOverlay">
          <LoadingContent message={`${selectedGridResolution} 격자 데이터 로딩 중`} />
        </div>
      )}
      <div className="leftOverlayRail">
        <SearchPanel
          onOpenGuide={() => setGuideOpen(true)}
          districts={districts}
          dongSearchEntries={dongSearchEntries}
          dongSearchLoading={dongSearchLoading}
          dongSearchError={dongSearchError}
          selectedDistrict={selectedDistrict}
          selectedLayer={selectedLayer}
          is3DMode={is3DOpen}
          isPolicyLayerLocked={isPolicyLayerLocked}
          selectedGridResolution={selectedGridResolution}
          selectedGridProperties={selectedGridProperties}
          gridSearchError={gridSearchError}
          gridSearchBusy={gridSearchBusy}
          onDistrictChange={handleDistrictChange}
          onDongSelect={handleDongSearch}
          onGridResolutionChange={handleGridResolutionChange}
          onLayerChange={handleLayerChange}
          onGridSearch={handleGridSearch}
        />
        {/* 범례는 rail의 맨 아래로 민다(CSS margin-top:auto). rail이 이미 뷰포트
            하단까지 닿아 있어 결과적으로 화면 좌하단에 붙고, rail 안에 있으니
            검색 패널이 범례를 덮지 않는다. rail 밖으로 빼면 좌하단에는 가지만
            데스크톱에서 왼쪽 패널 위로 겹친다. */}
        {!loading && !error && (
          <MapLegend
            layer={selectedLayer}
            layerLabel={getLayerLabel(selectedLayer)}
            colorOf={colorByLayerValue}
            quantile={isGuMode ? districtQuantiles : null}
          />
        )}
      </div>
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
        onBatchSimulationResult={handleBatchSimulationResult}
      />
      <AiChatLauncher
        selectedGridId={chatGridId}
        selectedDisplayGridId={chatDisplayGridId}
        selectedGuName={chatGuName}
      />
      {guideOpen && <OnboardingGuide onClose={() => setGuideOpen(false)} />}
    </div>
  );
}

interface SearchPanelProps {
  districts: DistrictOption[];
  dongSearchEntries: DongSearchIndexEntry[];
  dongSearchLoading: boolean;
  dongSearchError: string | null;
  selectedDistrict: string;
  selectedLayer: LayerKey;
  is3DMode: boolean;
  isPolicyLayerLocked: boolean;
  selectedGridResolution: GridResolution;
  selectedGridProperties: GridAnalysisProperties | null;
  gridSearchError: string | null;
  gridSearchBusy: boolean;
  onDistrictChange: (district: string) => void;
  onDongSelect: (entry: DongSearchIndexEntry) => void;
  onGridResolutionChange: (resolution: GridResolution) => void;
  onLayerChange: (layer: LayerKey) => void;
  onGridSearch: (code: string) => void;
  onOpenGuide: () => void;
}

function SearchPanel({
  districts,
  dongSearchEntries,
  dongSearchLoading,
  dongSearchError,
  selectedDistrict,
  selectedLayer,
  is3DMode,
  isPolicyLayerLocked,
  selectedGridResolution,
  selectedGridProperties,
  gridSearchError,
  gridSearchBusy,
  onDistrictChange,
  onDongSelect,
  onGridResolutionChange,
  onLayerChange,
  onGridSearch,
  onOpenGuide
}: SearchPanelProps) {
  const [locationQuery, setLocationQuery] = useState('');
  const [gridCodeInput, setGridCodeInput] = useState('');
  const [locationResultsOpen, setLocationResultsOpen] = useState(false);
  const [activeLocationResult, setActiveLocationResult] = useState(0);
  const locationSearchRowRef = useRef<HTMLDivElement | null>(null);
  const locationResultsRef = useRef<HTMLDivElement | null>(null);
  const [locationDropdownPosition, setLocationDropdownPosition] = useState<{
    top: number;
    left: number;
    width: number;
    maxHeight: number;
  } | null>(null);
  const locationResults = useMemo<LocationSearchResult[]>(() => {
    const rawQuery = locationQuery.trim();
    const query = normalizeLocationText(rawQuery);
    const queryParts = rawQuery.split(/\s+/).map(normalizeLocationText).filter(Boolean);
    if (!query) return [];

    const results: LocationSearchResult[] = [];
    const districtMatches = districts
      .filter((district) => normalizeLocationText(district.district).includes(query))
      .sort((left, right) => {
        const leftStarts = normalizeLocationText(left.district).startsWith(query) ? 0 : 1;
        const rightStarts = normalizeLocationText(right.district).startsWith(query) ? 0 : 1;
        return leftStarts - rightStarts || left.district.localeCompare(right.district, 'ko');
      });
    results.push(
      ...districtMatches.map((district) => ({ type: 'district' as const, district }))
    );

    const dongMatches = dongSearchEntries
      .filter((dong) => {
        const searchable = normalizeLocationText(`${dong.district}${dong.dong_name}`);
        return queryParts.every((part) => searchable.includes(part));
      })
      .sort((left, right) => {
        const leftStarts = normalizeLocationText(left.dong_name).startsWith(query) ? 0 : 1;
        const rightStarts = normalizeLocationText(right.dong_name).startsWith(query) ? 0 : 1;
        return (
          leftStarts - rightStarts ||
          left.district.localeCompare(right.district, 'ko') ||
          left.dong_name.localeCompare(right.dong_name, 'ko')
        );
      });
    results.push(...dongMatches.map((dong) => ({ type: 'dong' as const, dong })));
    return results.slice(0, 10);
  }, [districts, dongSearchEntries, locationQuery]);

  useEffect(() => {
    setActiveLocationResult(0);
  }, [locationQuery]);

  const showLocationResults = locationResultsOpen && Boolean(locationQuery.trim());

  useLayoutEffect(() => {
    if (!showLocationResults) {
      setLocationDropdownPosition(null);
      return;
    }

    const updatePosition = () => {
      const row = locationSearchRowRef.current;
      if (!row) return;
      const rect = row.getBoundingClientRect();
      const viewportPadding = 8;
      const dropdownGap = 5;
      const width = Math.min(rect.width, window.innerWidth - viewportPadding * 2);
      const left = Math.max(
        viewportPadding,
        Math.min(rect.left, window.innerWidth - width - viewportPadding)
      );
      const top = rect.bottom + dropdownGap;

      setLocationDropdownPosition({
        top,
        left,
        width,
        maxHeight: Math.max(0, Math.min(280, window.innerHeight - top - viewportPadding))
      });
    };

    updatePosition();
    window.addEventListener('resize', updatePosition);
    window.addEventListener('scroll', updatePosition, true);
    return () => {
      window.removeEventListener('resize', updatePosition);
      window.removeEventListener('scroll', updatePosition, true);
    };
  }, [showLocationResults]);

  const selectLocationResult = (result: LocationSearchResult) => {
    if (result.type === 'district') {
      setLocationQuery(result.district.district);
      onDistrictChange(result.district.district);
    } else if (result.type === 'dong') {
      setLocationQuery(`${result.dong.district} ${result.dong.dong_name}`);
      onDongSelect(result.dong);
    }
    setLocationResultsOpen(false);
  };

  const submitLocationSearch = () => {
    const result = locationResults[activeLocationResult] ?? locationResults[0];
    if (result) {
      selectLocationResult(result);
    }
  };
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
        <img className="heatPanelLogo" src="/logo-avatar.svg" alt="GA:ON" />
        <div>
          <p>Urban Heat Island</p>
          <h1>도시 열섬 해결 대시보드</h1>
        </div>
        {/* 가이드를 닫은 뒤에도 다시 찾을 수 있어야 한다. 챗봇 창을 옮기고 못 찾는
            문제를 더블클릭 초기화로 푼 것과 같은 이유다. */}
        <button
          className="heatPanelGuide"
          type="button"
          onClick={onOpenGuide}
          title="사용 가이드 다시 보기"
          aria-label="사용 가이드 다시 보기"
        >
          ?
        </button>
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
        <div
          className="locationSearch"
          onBlur={(event) => {
            const nextTarget = event.relatedTarget as Node | null;
            if (
              !event.currentTarget.contains(nextTarget) &&
              !locationResultsRef.current?.contains(nextTarget)
            ) {
              setLocationResultsOpen(false);
            }
          }}
        >
          <span>지역 검색</span>
          <div className="locationSearchControl">
            <div className="gcsRow" ref={locationSearchRowRef}>
              <input
                type="search"
                value={locationQuery}
                onChange={(event) => {
                  setLocationQuery(event.target.value);
                  setLocationResultsOpen(true);
                }}
                onFocus={() => setLocationResultsOpen(true)}
                onKeyDown={(event) => {
                  if (event.key === 'ArrowDown' && locationResults.length > 0) {
                    event.preventDefault();
                    setLocationResultsOpen(true);
                    setActiveLocationResult((current) =>
                      Math.min(current + 1, locationResults.length - 1)
                    );
                  } else if (event.key === 'ArrowUp' && locationResults.length > 0) {
                    event.preventDefault();
                    setActiveLocationResult((current) => Math.max(current - 1, 0));
                  } else if (event.key === 'Enter') {
                    event.preventDefault();
                    submitLocationSearch();
                  } else if (event.key === 'Escape') {
                    setLocationResultsOpen(false);
                  }
                }}
                placeholder="구·행정동 검색"
                aria-label="자치구 또는 행정동 검색"
                aria-controls="locationSearchResults"
                aria-expanded={locationResultsOpen && Boolean(locationQuery.trim())}
                aria-activedescendant={
                  locationResultsOpen && locationResults[activeLocationResult]
                    ? `locationResult-${activeLocationResult}`
                    : undefined
                }
                role="combobox"
                autoComplete="off"
                spellCheck={false}
                disabled={gridSearchBusy}
              />
              <button
                type="button"
                onClick={submitLocationSearch}
                disabled={gridSearchBusy || !locationQuery.trim()}
              >
                {gridSearchBusy ? '찾는 중' : '검색'}
              </button>
            </div>
            {showLocationResults &&
              locationDropdownPosition &&
              createPortal(
                <div
                  ref={locationResultsRef}
                  className="locationSearchResults"
                  id="locationSearchResults"
                  role="listbox"
                  style={locationDropdownPosition}
                >
                  {locationResults.map((result, index) => {
                    const resultKey =
                      result.type === 'district'
                        ? `district-${result.district.sigCode}`
                        : `dong-${result.dong.sig_code}-${result.dong.dong_name}`;
                    return (
                      <button
                        id={`locationResult-${index}`}
                        key={resultKey}
                        type="button"
                        role="option"
                        aria-selected={index === activeLocationResult}
                        className={index === activeLocationResult ? 'active' : undefined}
                        onMouseEnter={() => setActiveLocationResult(index)}
                        onClick={() => selectLocationResult(result)}
                      >
                        <span>
                          {result.type === 'district'
                            ? result.district.district
                            : `${result.dong.district} · ${result.dong.dong_name}`}
                        </span>
                        <small>
                          {result.type === 'district'
                            ? '자치구'
                            : '행정동'}
                        </small>
                      </button>
                    );
                  })}
                  {locationResults.length === 0 && (
                    <p>
                      {dongSearchLoading
                        ? '행정동 목록을 준비하고 있습니다…'
                        : '일치하는 서울 지역이 없습니다.'}
                    </p>
                  )}
                </div>,
                document.body
              )}
            {dongSearchError && <small className="locationSearchNotice">행정동 검색 불가</small>}
          </div>
        </div>
        <div className="gridCodeSearch">
          <span>격자 코드</span>
          <div className="gcsRow">
            <input
              type="text"
              value={gridCodeInput}
              onChange={(event) => setGridCodeInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== 'Enter') return;
                event.preventDefault();
                onGridSearch(gridCodeInput);
              }}
              placeholder="격자 코드를 입력하세요"
              aria-label="현재 격자 해상도의 격자 코드 검색"
              autoComplete="off"
              spellCheck={false}
              disabled={gridSearchBusy}
            />
            <button
              type="button"
              onClick={() => onGridSearch(gridCodeInput)}
              disabled={gridSearchBusy || !gridCodeInput.trim()}
            >
              {gridSearchBusy ? '찾는 중' : '검색'}
            </button>
          </div>
        </div>
      </div>
      {gridSearchError && (
        <p className="gcsError" role="alert">
          {gridSearchError}
        </p>
      )}

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
        {/* 버튼만 비활성으로 두면 왜 안 눌리는지 알 수 없다. 이유와 푸는 법을 함께 적는다. */}
        {(is3DMode || isPolicyLayerLocked) && (
          <p className="indicatorLockNote" role="status">
            {is3DMode
              ? '3D에서는 지표면온도만 볼 수 있어요.'
              : '정책 결과를 보는 동안은 지표면온도로 고정돼요. 지도에서 격자를 클릭하면 풀립니다.'}
          </p>
        )}
        <div className="indicatorList">
          {INDICATOR_GROUPS.map((group) => (
            <div className="indicatorGroup" key={group.key}>
              <h2>{group.label}</h2>
              <div>
                {HEAT_ISLAND_INDICATORS.filter(
                  (indicator) => indicator.group === group.key
                ).map((indicator) => (
                  <button
                    key={indicator.key}
                    type="button"
                    className={selectedLayer === indicator.key ? 'active' : ''}
                    disabled={
                      (is3DMode || isPolicyLayerLocked) && indicator.key !== LST_LAYER
                    }
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
                        ? formatLayerProperty(
                            selectedGridProperties,
                            indicator.key,
                            isGuResolution(selectedGridResolution)
                          )
                        : '선택 전'}
                    </b>
                  </button>
                ))}
              </div>
            </div>
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
