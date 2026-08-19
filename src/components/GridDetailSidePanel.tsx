import { useEffect, useRef, useState } from 'react';
import PolicyCaseModal from './PolicyCaseModal';
import { POLICY_CASES } from '../config/policyCases';
import {
  ApiRequestError,
  getAiFeatureCatalog,
  simulateAggregateGridPolicy,
  simulateDistrictGridPolicy,
  simulateGridPolicy,
  simulateSeoulGridPolicy,
  simulateScopedGridPolicy
} from '../services/api';
import {
  POLICY_FEATURE_LABELS,
  policyApplicability,
  policySimulationRequest,
  POLICY_ICONS,
  POLICY_SHORT_NAMES,
  usePolicyPresets
} from '../config/policyPresets';
import type {
  BatchSimulationResponse,
  FeatureRange,
  GridAnalysisProperties,
  GridResolution,
  PolicyFeature,
  PolicyPreset,
  SimulationPolicyScope,
  SimulationChangedFeature
} from '../types/dashboard';

interface Props {
  properties: GridAnalysisProperties | null;
  // 선택 격자가 속한 구의 전체 격자 수 (priority_rank의 분모). 없으면 순위만 표시.
  guGridTotal: number | null;
  selectedDistrict: string;
  // 분석 지역이 자치구일 때 그 구의 코드. 격자를 고르지 않아도 '구 전체' 정책을
  // 돌릴 수 있어야 해서, 선택 격자의 gu_code와 별개로 받는다.
  selectedDistrictCode: string | null;
  selectedGridResolution: GridResolution;
  isOpen: boolean;
  onToggle: () => void;
  // MapDashboard의 formatAnyProperty를 그대로 받아 단위 포맷(%·℃·m·㎡)을 재사용한다.
  formatValue: (
    properties: GridAnalysisProperties | null,
    key: keyof GridAnalysisProperties
  ) => string;
  // Phase 3 — 격자 비교. 두 번째(비교) 격자와 그 선택 흐름.
  compareProperties: GridAnalysisProperties | null;
  isPickingCompare: boolean;
  onStartCompare: () => void;
  onClearCompare: () => void;
  onBatchSimulationResult: (result: BatchSimulationResponse | null) => void;
}

// anomaly(구 평균 대비 ℃)로 라벨·색톤·막대 채움(%)을 정한다.
// 경계값은 지도(MapDashboard.tsx colorByLayerValue의 mean_actual_anomaly)와 동일: 0 / 1.5 / 3
//   < 0 초록=비교적 시원 · 0~1.5 노랑=보통 · 1.5~3 주황=다소 더움 · ≥3 빨강=열 위험 높음
function heatLevel(anomaly?: number) {
  if (anomaly == null) return { label: '분석중', tone: 'neutral' as const, fill: 50 };
  const fill = Math.max(6, Math.min(94, Math.round(50 + anomaly * 12)));
  if (anomaly >= 3)   return { label: '열 위험 높음', tone: 'danger' as const, fill };
  if (anomaly >= 1.5) return { label: '다소 더움',   tone: 'hot' as const, fill };
  if (anomaly >= 0)   return { label: '보통',        tone: 'neutral' as const, fill };
  return { label: '비교적 시원', tone: 'cool' as const, fill };
}


// SHAP feature 키 → 한글 라벨 (없으면 원문 키 그대로)
const FEATURE_LABELS: Record<string, string> = {
  impervious_ratio: '불투수면 비율',
  building_ratio: '건물 비율',
  green_ratio: '녹지율',
  ndvi: '식생지수(NDVI)',
  albedo: '알베도(반사율)',
  road_ratio: '도로 비율',
  avg_ground_floor_count: '평균 지상층수',
  max_ground_floor_count: '최대 지상층수',
  floor_area_ratio_proxy: '용적률(연면적비)',
  elevation_m: '고도',
  slope_deg: '경사',
  nearest_park_distance_m: '공원까지 거리',
  park_area_within_500m: '주변 공원 면적',
  nearest_stream_distance_m: '하천까지 거리',
  zoning_residential_ratio: '주거지역 비율',
  zoning_commercial_ratio: '상업지역 비율',
  zoning_industrial_ratio: '공업지역 비율',
  zoning_green_ratio: '용도지역 녹지 비율',
  building_shadow_proxy: '건물 그림자',
  hardscape_exposure_proxy: '포장면 노출',
  built_heat_proxy: '지표 발열'
};
function featureLabel(key?: string) {
  return key ? FEATURE_LABELS[key] ?? key : '';
}

// 모델이 예측에 쓰는 핵심 피처. 이 중 하나라도 값이 없으면(위성·지형 데이터 결측) 모델이
// 그 격자를 온전히 예측할 수 없어 SHAP·시뮬레이션 결과를 신뢰할 수 없다. 지도에 뜨는 격자
// 중 약 33개(대부분 한강 수면·경계). 백엔드 데이터셋에서 NaN인 격자에 해당.
const CORE_MODEL_FEATURES: (keyof GridAnalysisProperties)[] = [
  'green_ratio',
  'ndvi',
  'impervious_ratio',
  'albedo',
  'elevation_m',
  'building_ratio'
];
// ★ null 과 undefined 를 구분한다. 느슨한 v == null 로 두면 둘 다 걸린다.
//
// 격자를 클릭하면 먼저 지도용 geojson 의 속성이 패널에 들어간다. 이건 속성이
// 13개뿐이라 albedo·elevation_m 이 아예 없다(상세 geojson 은 53개). 구별 상세
// 데이터(1MB·약 0.35초)가 도착하면 교체되는데, 그 사이에 두 필드가 undefined 라
// "데이터 불완전 격자" 경고가 떴다가 사라졌다. 로컬은 이 구간이 순식간이라
// 안 보이고 배포 환경에서만 보였다.
//
// 두 상태는 구분할 수 있다. 진짜 결측 격자는 키가 있고 값이 null 이며(확인:
// 11680_02607 의 albedo·elevation_m 이 키 있음 + null), 아직 안 온 필드는 키
// 자체가 없어 undefined 다. 그래서 null 만 결측으로 센다.
function isIncompleteGrid(properties: GridAnalysisProperties | null): boolean {
  if (!properties) return false;
  return CORE_MODEL_FEATURES.some((key) => {
    const v = properties[key];
    if (v === undefined) return false;
    return v === null || (typeof v === 'number' && !Number.isFinite(v));
  });
}

// 일반인이 이해하기 어려운 지표 설명 (툴팁용)
const METRIC_DESC: Record<string, string> = {
  impervious_ratio: '빗물이 스며들지 못하는 포장·건물 등 인공 지표면 비율. 높을수록 낮에 열을 머금어 더 뜨겁습니다.',
  building_ratio: '격자에서 건물이 땅을 덮은 비율(건폐율). 높을수록 열 축적·복사가 커집니다.',
  green_ratio: '나무·풀 등 녹지가 덮은 면적 비율. 높을수록 그늘·수분 증발로 시원합니다.',
  ndvi: '식생 활력 지수(−1~1). 높을수록 식물이 많고 건강해 냉각 효과가 큽니다.',
  albedo: '지표면이 햇빛을 반사하는 정도(0~1). 높을수록 덜 데워집니다.',
  road_ratio: '도로가 덮은 면적 비율. 아스팔트는 열을 잘 머금습니다.',
  avg_ground_floor_count: '건물들의 평균 층수. 높으면 밀집·복사열이 커질 수 있습니다.',
  max_ground_floor_count: '격자에서 가장 높은 건물의 층수. 고층은 그늘을 드리워 지표를 식히기도 합니다.',
  floor_area_ratio_proxy: '건물 총량(연면적) 추정치로 용적률과 유사합니다. 높을수록 개발밀도가 큽니다.',
  elevation_m: '해발 고도(m). 보통 높을수록 기온이 낮습니다.',
  slope_deg: '지형의 경사(도). 보통 가파를수록 기온이 낮은 경향이 있습니다.',
  nearest_park_distance_m: '가장 가까운 공원까지의 거리(m). 가까울수록 냉각 혜택을 받습니다.',
  park_area_within_500m: '반경 500m 안의 공원 면적(㎡).',
  nearest_stream_distance_m: '가장 가까운 하천까지 거리(m). 가까울수록 물가 냉각 효과를 받습니다.',
  zoning_residential_ratio: '주거 용도지역으로 지정된 비율.',
  zoning_commercial_ratio: '상업 용도지역으로 지정된 비율. 상업지는 열이 높은 경향이 있습니다.',
  zoning_industrial_ratio: '공업 용도지역으로 지정된 비율.',
  zoning_green_ratio: '용도지역상 녹지로 지정된 비율.',
  building_shadow_proxy: '건물비율×평균층수로 추정한 그늘/복사 지표.',
  hardscape_exposure_proxy: '포장면이 녹지 없이 노출된 정도.',
  built_heat_proxy: '인공 지표면이 식생 없이 열을 내뿜는 정도.'
};

// 값의 출처. "이 숫자를 어디서 가져왔나"를 물었을 때 답할 수 있어야 신뢰가 생기고,
// 반대로 출처를 모르면 위성 추정값을 실측처럼 읽는다.
// 출처는 GAON/docs/GAON_ML_전과정_정리_ko.md §4.2, 수집 스크립트 주석과 일치시킨다.
const METRIC_SOURCE: Record<string, string> = {
  impervious_ratio: '위성 Dynamic World (Google Earth Engine)',
  green_ratio: '위성 Dynamic World (Google Earth Engine)',
  ndvi: '위성 Sentinel-2 (Google Earth Engine)',
  albedo: '위성 Landsat 표면반사율 (Google Earth Engine)',
  building_ratio: '국토부 VWorld 건물 도형 (lt_c_spbd)',
  avg_ground_floor_count: '국토부 VWorld 건물 도형',
  max_ground_floor_count: '국토부 VWorld 건물 도형',
  floor_area_ratio_proxy: '국토부 VWorld 건물 도형에서 파생 (바닥면적×층수÷격자면적)',
  road_ratio: '국토부 VWorld 도로 (lt_c_upisuq151)',
  nearest_park_distance_m: '국토부 VWorld 공원 (lt_c_upisuq161)',
  park_area_within_500m: '국토부 VWorld 공원',
  nearest_stream_distance_m: '국토부 VWorld 하천 (lt_c_wkmstrm)',
  zoning_residential_ratio: '국토부 VWorld 용도지역 (lt_c_uq111)',
  zoning_commercial_ratio: '국토부 VWorld 용도지역',
  zoning_industrial_ratio: '국토부 VWorld 용도지역',
  zoning_green_ratio: '국토부 VWorld 용도지역',
  elevation_m: '위성 SRTM DEM (Google Earth Engine)',
  slope_deg: '위성 SRTM DEM (Google Earth Engine)',
  mean_actual_lst: '위성 Landsat 8/9 열적외 밴드(ST_B10)',
  mean_actual_anomaly: '위성 Landsat 8/9 열적외 밴드(ST_B10)',
  nearest_shelter_distance_m: '서울 열린데이터 무더위쉼터 4,088개'
};

// 관측 시점. 위성 시계열만 기간이 명확하고 나머지는 수집 시점의 최신본이라 '기준 시점'으로 쓴다.
const LST_PERIOD = '2023~2025년 여름(6~8월) 관측 평균';
const METRIC_PERIOD: Record<string, string> = {
  mean_actual_lst: LST_PERIOD,
  mean_actual_anomaly: LST_PERIOD,
  ndvi: LST_PERIOD,
  albedo: LST_PERIOD,
  green_ratio: LST_PERIOD,
  impervious_ratio: LST_PERIOD
};

// 인구는 '이 격자를 세어본 값'이 아니라 구 단위 통계를 건물로 나눠준 추정치다.
// 수집 스크립트(collect_grid_vulnerability.py)가 "정직한 한계(반드시 인지)"로 못박아 둔
// 내용 — 구 안에서 이 값의 순위는 주거 연면적 순위와 같다 — 을 그대로 옮긴다.
// 이걸 모르면 '320명'을 조사 결과처럼 읽는다.
const POPULATION_TIP =
  '이 격자를 직접 센 값이 아니라, 자치구 인구를 주거 연면적에 비례해 나눠준 추정치예요.\n\n' +
  '쓰는 인구는 주민등록인구가 아니라 생활인구예요. 그 시간 실제로 그 지역에 있는 ' +
  '사람(출퇴근·방문자 포함)이라, 폭염 노출 위험엔 이쪽이 더 맞습니다.\n\n' +
  '한계 · 구 안에서 이 값의 순위는 주거 연면적 순위와 같아요. 건물이 없는 격자는 0명이 ' +
  '됩니다. 실제 거주 여부와는 다를 수 있어요.\n\n' +
  '출처 · 서울 열린데이터 생활인구(자치구, 전 시간대 평균)';

const ELDERLY_TIP =
  '추정 거주 인구에 그 격자가 속한 행정동의 65세 이상 비율을 곱한 값이에요.\n\n' +
  '고령비율은 자치구가 아니라 행정동(서울 426개) 단위라, 같은 구 안에서도 동마다 ' +
  '다릅니다. 실제로 6.2%~36.4%까지 벌어져요.\n\n' +
  '한계 · 인구 자체가 추정치라 이 값도 추정치입니다.\n\n' +
  '출처 · 통계청 SGIS 행정동 경계 + SGIS 인구통계';

function metricTip(feature: string): string {
  const parts = [METRIC_DESC[feature]].filter(Boolean) as string[];
  const source = METRIC_SOURCE[feature];
  const period = METRIC_PERIOD[feature];
  if (source) parts.push(`출처 · ${source}`);
  if (period) parts.push(`기준 · ${period}`);
  return parts.join('\n\n');
};

// ⓘ 아이콘에 마우스를 올리면 설명 말풍선을 띄우는 커스텀 툴팁
// align: 말풍선이 열리는 방향 (아이콘이 패널 왼쪽이면 'left'=오른쪽으로 펼침, 오른쪽이면 'right')
// down: 기본은 위로 열리는데, 카드가 패널 상단에 있으면 말풍선이 잘린다. 그럴 때 아래로 연다.
function InfoTip({
  text,
  align = 'left',
  down = false
}: {
  text: string;
  align?: 'left' | 'center' | 'right';
  down?: boolean;
}) {
  return (
    <span
      className={`infotip infotip-${align}${down ? ' infotip-down' : ''}`}
      tabIndex={0}
      aria-label={text}
    >
      i<span className="infotip-bubble" role="tooltip">{text}</span>
    </span>
  );
}

// SVG 도넛: 비율(0~1)을 원호로. pathLength=100 트릭으로 dasharray를 퍼센트처럼 쓴다.
function Donut({
  value,
  color,
  label,
  tip,
  tipAlign,
  estimated
}: {
  value?: number | null;
  color: string;
  label: string;
  tip?: string;
  tipAlign?: 'left' | 'center' | 'right';
  estimated?: boolean;
}) {
  const pct = value != null ? Math.max(0, Math.min(100, value * 100)) : 0;
  return (
    <div className="donut">
      <div className="d-wrap">
        <svg width="80" height="80" viewBox="0 0 80 80">
          <circle cx="40" cy="40" r="33" fill="none" stroke="var(--gdp-sub2)" strokeWidth="8" />
          <circle
            cx="40"
            cy="40"
            r="33"
            fill="none"
            stroke={color}
            strokeWidth="8"
            strokeLinecap="round"
            pathLength={100}
            strokeDasharray={`${pct} 100`}
          />
        </svg>
        <div className="d-num">{value != null ? `${Math.round(pct)}%` : '—'}</div>
      </div>
      <div className="d-lab">
        {label}
        {estimated ? ' (추정)' : ''}
        {tip && <InfoTip text={tip} align={tipAlign} />}
      </div>
    </div>
  );
}

/**
 * 요약 카드 → 시뮬레이션 카드로 스크롤.
 *
 * `zoom: var(--ui-scale)`이 걸린 컨테이너라 함정이 두 개다.
 *
 * 1. 좌표계 — getBoundingClientRect는 배율이 곱해진 화면 px를 주는데 scrollTop은
 *    배율 이전 CSS px이다. 이동량을 배율로 나눠야 한다. 배율은 --ui-scale을 읽는
 *    대신 실제 렌더 높이 / 레이아웃 높이로 구한다(그 값은 JS가 계속 덮어쓴다).
 * 2. 부드러운 스크롤 — `behavior: 'smooth'`도 `scrollIntoView`도 이 컨테이너에서는
 *    아예 듣지 않는다(실측: 1,027px 가야 하는데 0~65px에서 섰다). scrollTop 직접
 *    대입은 정상이라, 애니메이션을 직접 돌린다.
 */
function scrollToSimulation() {
  const card = document.getElementById('gdpSimCard');
  const scroller = card?.closest('.rightPanelDashboard');
  if (!card || !(scroller instanceof HTMLElement)) return;

  const scale = card.getBoundingClientRect().height / card.offsetHeight || 1;
  const delta =
    (card.getBoundingClientRect().top - scroller.getBoundingClientRect().top) / scale;
  const from = scroller.scrollTop;
  const to = Math.min(from + delta - 8, scroller.scrollHeight - scroller.clientHeight);

  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    scroller.scrollTop = to;
    return;
  }

  const duration = 420;
  const start = performance.now();
  const step = (now: number) => {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - (1 - t) ** 3;
    scroller.scrollTop = from + (to - from) * eased;
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// 빈 상세 패널의 제목은 '무엇을 누르라'는 행동만, 아래 설명은 '지금 어디를 보고
// 있는지'만 말한다. 둘 다 "클릭하세요"로 쓰면 같은 말이 두 줄 반복된다.
function selectionTitle(district: string, resolution: GridResolution) {
  if (district === '전체' && resolution !== '100m') {
    return '지도에서 지역을 클릭해 주세요';
  }
  return '지도에서 격자를 클릭해 주세요';
}

function selectionPrompt(district: string, resolution: GridResolution) {
  if (district === '전체' && resolution === '100m') {
    return '지금 서울 전체 격자를 보고 있어요.';
  }
  if (district === '전체') {
    return '지역을 누르면 그 안의 격자를 볼 수 있어요.';
  }
  return `지금 ${district} 범위를 보고 있어요.`;
}

function GridDetailSidePanel({
  properties,
  guGridTotal,
  selectedDistrict,
  selectedDistrictCode,
  selectedGridResolution,
  isOpen,
  onToggle,
  formatValue,
  compareProperties,
  isPickingCompare,
  onStartCompare,
  onClearCompare,
  onBatchSimulationResult
}: Props) {
  const fmt = (key: keyof GridAnalysisProperties) => formatValue(properties, key);
  // 격자를 고르지 않았을 때만 넓은 범위 정책 카드를 띄운다.
  // 격자를 고르면 아래 SimulationCard가 그 격자 기준 정책 시나리오를 맡는다 —
  // 둘을 같이 두면 '정책 시나리오'가 두 벌이 되어 어느 쪽을 돌리는지 알 수 없다.
  // 범위는 좌측 '분석 지역'을 따라간다: 자치구면 그 구 전체, '전체'면 서울시 전체.
  const wideAreaPolicyScope: 'seoul' | 'district' | null = properties
    ? null
    : selectedDistrict === '전체'
    ? 'seoul'
    : selectedDistrictCode
    ? 'district'
    : null;
  const showWideAreaPolicyCard = wideAreaPolicyScope !== null;
  const wideAreaPolicyLabel =
    wideAreaPolicyScope === 'district' ? `${selectedDistrict} 전체` : '서울시 전체';
  const guLabel = properties
    ? [properties.gu_name, properties.dong_name].filter(Boolean).join(' ')
    : '';
  const gridId = properties?.display_grid_id ?? properties?.grid_id ?? '';
  // AI 채팅 문맥(selectedGridId 등) 계산은 MapDashboard로 옮겼다(#26).
  const lst = properties?.mean_actual_lst;
  const anomaly = properties?.mean_actual_anomaly;
  const level = heatLevel(anomaly);
  // 핵심 피처 결측 격자면 모델 기반 분석(SHAP·시뮬레이션)을 신뢰불가로 처리한다.
  // 위성 온도 관측이 없는 격자(lst_observed === false)도 분석 불가로 묶는다.
  // 값 자체가 없어 온도·순위·시뮬레이션이 성립하지 않는다.
  const lstMissing = properties?.lst_observed === false;
  const incomplete = isIncompleteGrid(properties) || lstMissing;
  // 요약 카드의 '이 격자 개선해보기' 버튼 조건. SimulationCard의 canSimulate와
  // 같아야 한다 — 결측 격자(예: 11680_02607)에서 버튼만 남으면 눌러도
  // '시뮬레이션을 제공하지 않아요' 문구로 떨어진다.
  const hasSimulationTarget = properties
    ? selectedGridResolution === '250m' || selectedGridResolution === '500m'
      ? typeof properties.display_grid_id === 'string' &&
        properties.display_grid_id.length > 0
      : typeof properties.grid_id === 'string' && properties.grid_id.length > 0
    : false;
  const canSimulate = hasSimulationTarget && !incomplete;
  // 우선순위: 구 내부 순위(rank) + 분모(total)로 백분위(pct)를 낸다.
  // rank 1 = 가장 시급 → 상위 1%. rank가 클수록(=뒤쪽) 개선 급하지 않은 격자.
  const rank = properties?.priority_rank;
  const total = guGridTotal;
  const pct =
    rank != null && total ? Math.max(1, Math.round((rank / total) * 100)) : null;
  const buildingEstimated = properties?.building_ratio_estimated === true;
  const greenDelta = properties?.green_delta_c;   // 녹지확대 시 저감효과 (음수=냉각)
  const pop = properties?.est_population;          // 추정 거주 인구
  // 건물 도형 누락 특수지역(군사·공공 등)은 인구 추정이 0/없음 → "집계 제외"로 안내
  const popUnavailable = buildingEstimated && (pop == null || pop === 0);
  const elderly = properties?.est_elderly;         // 추정 고령인구(65+)
  const elderlyText =
    elderly != null
      ? `${Math.round(elderly).toLocaleString()}명${
          pop ? ` · ${Math.round((elderly / pop) * 100)}%` : ''
        }`
      : '—';
  // 가장 가까운 무더위쉼터 이름·주소 (빈 문자열이면 없는 것으로 처리)
  const shelterName = properties?.nearest_shelter_name?.trim() || null;
  const shelterAddr = properties?.nearest_shelter_addr?.trim() || null;
  const shelterTip =
    (shelterAddr ? `주소: ${shelterAddr}\n\n` : '') +
    '폭염 때 이용할 수 있는 냉방 대피 시설이에요. 가까울수록 취약계층 대응에 유리합니다.\n\n' +
    '거리는 격자 중심에서 잰 직선거리라 실제 도보 거리와는 다를 수 있어요.\n\n' +
    '출처 · 서울 열린데이터 무더위쉼터 4,088개';

  // SHAP top1~3: 존재하는 것만 모으고, 막대 길이용 최대 절대기여도를 구한다.
  const shapItems = [
    { f: properties?.top1_feature, v: properties?.top1_shap },
    { f: properties?.top2_feature, v: properties?.top2_shap },
    { f: properties?.top3_feature, v: properties?.top3_shap }
  ].filter((it): it is { f: string; v: number } =>
    typeof it.f === 'string' && typeof it.v === 'number'
  );
  const maxShap = shapItems.reduce((m, it) => Math.max(m, Math.abs(it.v)), 0) || 1;

  return (
    <aside
      className={[
        'gridDetailSidePanel',
        isOpen ? '' : 'collapsed'
      ].filter(Boolean).join(' ')}
    >
      <button
        type="button"
        className="sidePanelToggle"
        aria-label={isOpen ? '격자 상세 패널 닫기' : '격자 상세 패널 열기'}
        aria-expanded={isOpen}
        onClick={onToggle}
      >
        {isOpen ? '›' : '‹'}
      </button>

      <div className="sidePanelBody">
        <section className="rightPanelDashboard">
          <div className="gridReport">
            {properties ? (
            <>
            <div className="card">
              <div className="eyebrow">
                {selectedGridResolution === '250m' || selectedGridResolution === '500m'
                  ? `${selectedGridResolution} 집계 격자 상세`
                  : '격자 상세'}
              </div>
              <div className="gu">{guLabel || '선택 격자'}</div>
              <div className="id">{gridId} · 면적 {fmt('area_m2')}</div>

              <div className="hero-main">
                <div>
                  <div className={`ht-num ${level.tone}`}>
                    {lst != null ? lst.toFixed(1) : '—'}<span>℃</span>
                  </div>
                  {/* 값이 없을 때 '낮음'이라고 단정하지 않는다. 위성 관측이 없는
                      격자에서 '— 낮음'으로 나와 사실과 다른 말이 됐다. */}
                  <div className="ht-sub">
                    {anomaly != null ? (
                      <>
                        지표면온도 · 구 평균보다{' '}
                        <b className={level.tone}>
                          {`${anomaly > 0 ? '+' : ''}${anomaly.toFixed(1)}℃`}
                        </b>{' '}
                        {anomaly >= 0 ? '높음' : '낮음'}
                      </>
                    ) : (
                      '지표면온도 · 관측값 없음'
                    )}
                  </div>
                </div>
                <span className={`risk ${level.tone}`}>{level.label}</span>
              </div>

              <div className="heatbar">
                <div className={`hb ${level.tone}`} style={{ width: `${level.fill}%` }} />
              </div>
              <div className="hb-lab"><span>시원</span><span>뜨거움</span></div>

              {rank != null && (
                <div className="chips">
                  {pct != null && pct <= 50 && (
                    <span className="chip">개선 우선 상위 {pct}%</span>
                  )}
                  <span className="chip ghost">
                    구 내 {rank.toLocaleString()}위
                    {total ? ` / ${total.toLocaleString()}` : ''}
                  </span>
                </div>
              )}

              {/* 이 서비스의 핵심 기능인 시뮬레이션이 패널 아래에서 세 번째라
                  스크롤해야 보였다. 요약 카드에서 바로 내려갈 수 있게 한다. */}
              {canSimulate && (
                <button className="gdpSimJump" type="button" onClick={scrollToSimulation}>
                  <span>
                    이 격자 개선해보기
                    <small>정책 6종 비교 · 직접 시뮬레이션</small>
                  </span>
                  <b aria-hidden="true">↓</b>
                </button>
              )}
            </div>

            {incomplete && (
              <div className="card gdpIncomplete">
                <div className="sec-title">⚠ 데이터 불완전 격자</div>
                {lstMissing ? (
                  <p className="gdpNote">
                    이 격자는 위성 지표면온도 관측이 없어 온도·순위·시뮬레이션을 제공하지
                    않습니다. 아래 환경 프로필(녹지·건물·식생 등)은 정상 값이에요.
                    (서울 전체 102개 · 약 0.16%)
                  </p>
                ) : (
                  <p className="gdpNote">
                    이 격자는 위성·지형 데이터 일부가 없어 모델이 온전히 분석할 수 없어요.
                    온도 영향 요인(TOP)과 시뮬레이션은 신뢰도가 낮아 제공하지 않습니다.
                    (서울 전체의 약 0.05% · 대부분 한강 수면·경계 격자)
                  </p>
                )}
              </div>
            )}

            <div className="tiles">
              <div className="tile">
                <div className="t-lab">
                  {/* 라벨 칸이 110px이다. '녹지화 시 저감 가능'은 123px이라 두 줄로 접히면서
                      '능' 한 글자와 ⓘ만 둘째 줄에 남았다. '녹지화 시 저감'은 109px로 1px밖에
                      안 남아 윈도우/맥 폰트 차이에서 바로 깨진다. 105px인 이 라벨을 쓴다. */}
                  녹지화 저감량
                  {/*
                    "모든 격자에 같은 조건" 이라고 쓰면 안 된다.
                    build_seoul_dashboard.py 의 (Xs[c] + d).clip(0, 1) 때문에
                    불투수면이 이미 5%p 미만인 격자는 -5%p 를 다 받지 못하고 0 에서 멈춘다.
                    서울 64,544격자 중 9,208개(14.3%)가 여기 해당하고, 그 격자들이
                    '녹지화 여지' 상위 10% 의 35.2% 를 차지한다(전체 비율의 2.5배).
                    반면 지도 레이어인 priority_score 상위 10% 의 clip 비율은 0.2% 라
                    순위로는 거의 전파되지 않는다. 근거: analysis/check_priority_contamination.py
                  */}
                  <InfoTip text="녹지율 +5%p · NDVI +0.03 · 불투수면 −5%p 를 함께 적용했을 때 모델이 예측한 온도 변화입니다. 다만 불투수면이 이미 5%p 미만인 격자(서울의 14.3%)는 감소분을 다 받지 못해, 이 값으로 격자끼리 순위를 매기면 그런 격자가 실제보다 유리하게 나옵니다. 지도의 '개선 우선순위'는 이 영향을 거의 받지 않습니다. 아래 직접 시뮬레이션은 원하는 값을 넣어보는 것이라 결과가 다를 수 있습니다." />
                </div>
                <div className={`t-num ${greenDelta != null && greenDelta < 0 ? 'cool' : ''}`}>
                  {greenDelta != null ? fmt('green_delta_c') : '—'}
                </div>
              </div>
              <div className="tile">
                <div className="t-lab">추정 거주 인구</div>
                {popUnavailable ? (
                  <div className="t-num t-na">집계 제외</div>
                ) : (
                  <div className="t-num">
                    {pop != null ? `${Math.round(pop).toLocaleString()}명` : '—'}
                  </div>
                )}
              </div>
            </div>
            {shapItems.length > 0 && !incomplete && (
              <div className="card">
                <div className="sec-title">온도에 영향이 큰 요인 TOP {shapItems.length}</div>
                <div className="shap">
                  {shapItems.map((it) => {
                    const pos = it.v >= 0;
                    const width = Math.round((Math.abs(it.v) / maxShap) * 46);
                    // 이 피처의 현재값이 격자 데이터에 있는지 확인. SHAP는 19개 모델 피처 중
                    // 아무거나 top으로 뽑지만 대시보드 geojson엔 일부만 저장돼 있어, 값이 없으면
                    // fmt()가 '데이터 준비중'을 반환한다. 값이 없을 땐 '현재 …' 표기를 생략해
                    // 이름·기여도(℃, 항상 유효)만 보여준다.
                    const rawValue = properties?.[it.f as keyof GridAnalysisProperties];
                    const hasValue =
                      typeof rawValue === 'number'
                        ? Number.isFinite(rawValue)
                        : rawValue != null && rawValue !== '';
                    return (
                      <div className="shap-item" key={it.f}>
                        <div className="si-top">
                          <span className="si-name">
                            {featureLabel(it.f)}
                            {METRIC_DESC[it.f] && <InfoTip text={metricTip(it.f)} />}
                          </span>
                          {hasValue && (
                            <span className="si-actual">
                              현재 <b>{fmt(it.f as keyof GridAnalysisProperties)}</b>
                            </span>
                          )}
                        </div>
                        <div className="si-bot">
                          <div className="track">
                            <div
                              className={pos ? 'bar pos' : 'bar neg'}
                              style={{ width: `${width}%` }}
                            />
                          </div>
                          <span className={`shap-val ${pos ? 'hot' : 'cool'}`}>
                            {pos ? '+' : '−'}
                            {Math.abs(it.v).toFixed(2)}℃
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="legend">
                  <span>
                    <span className="dot" style={{ background: 'var(--gdp-hot)' }} />
                    <b>온도 올림</b>
                  </span>
                  <span>
                    <span className="dot" style={{ background: 'var(--gdp-cool)' }} />
                    <b>온도 내림</b>
                  </span>
                  <span className="muted">막대 = 기여도(℃)</span>
                </div>
              </div>
            )}

            <div className="card">
              <div className="sec-title">환경 프로필</div>
              <div className="donuts">
                <Donut
                  value={properties.green_ratio}
                  color="var(--gdp-green)"
                  label="녹지율"
                  tip={metricTip('green_ratio')}
                  tipAlign="left"
                />
                <Donut
                  value={properties.impervious_ratio}
                  color="var(--gdp-hot)"
                  label="불투수면"
                  tip={metricTip('impervious_ratio')}
                  tipAlign="center"
                />
                <Donut
                  value={properties.building_ratio}
                  color="var(--gdp-ink2)"
                  label="건물"
                  tip={metricTip('building_ratio')}
                  tipAlign="right"
                  estimated={buildingEstimated}
                />
              </div>
              {buildingEstimated && (
                <p className="gdpNote">
                  건물 비율은 건물 도형(VWorld) 데이터가 없어 위성 지표면(built-up)으로 추정한 값이에요.
                </p>
              )}
            </div>

            <div className="card">
              <div className="sec-title">취약성</div>
              <div className="rows">
                <div className="row">
                  <span className="r-lab">
                    추정 거주 인구
                    <InfoTip text={POPULATION_TIP} align="center" />
                  </span>
                  <span className="r-val">
                    {popUnavailable
                      ? '집계 제외'
                      : pop != null
                      ? `${Math.round(pop).toLocaleString()}명`
                      : '—'}
                  </span>
                </div>
                <div className="row">
                  <span className="r-lab">
                    추정 고령인구 (65+)
                    <InfoTip text={ELDERLY_TIP} align="center" />
                  </span>
                  <span className="r-val">{popUnavailable ? '집계 제외' : elderlyText}</span>
                </div>
                <div className="shelter">
                  <div className="shelter-head">
                    가장 가까운 무더위쉼터
                    <InfoTip text={shelterTip} align="center" />
                  </div>
                  <div className="shelter-main">
                    <span className="shelter-name">{shelterName ?? '정보 없음'}</span>
                    <span className="shelter-dist">{fmt('nearest_shelter_distance_m')}</span>
                  </div>
                </div>
              </div>
              {popUnavailable && (
                <p className="gdpNote">
                  군사·공공 등 특수지역이라 추정 인구·고령인구가 집계되지 않았어요.
                </p>
              )}
            </div>

            {/* 직접 시뮬레이션 (취약성 바로 아래) */}
            <SimulationCard
              properties={properties}
              selectedDistrict={selectedDistrict}
              selectedDistrictCode={selectedDistrictCode}
              selectedGridResolution={selectedGridResolution}
              incomplete={incomplete}
              onBatchSimulationResult={onBatchSimulationResult}
            />

            {/* 다른 격자와 비교 (Phase 3) */}
            <ComparisonCard
              properties={properties}
              compareProperties={compareProperties}
              isPickingCompare={isPickingCompare}
              onStartCompare={onStartCompare}
              onClearCompare={onClearCompare}
              formatValue={formatValue}
            />
            </>
          ) : (
            <>
            {/* 빈 화면과 정책 카드가 서로 남처럼 놓여 있었다. 할 수 있는 일을 순서로
                묶고, 마지막 단계가 바로 아래 정책 카드를 가리키게 해서 이어 붙인다. */}
            <div
              className={`gridReportEmpty${showWideAreaPolicyCard ? ' withPolicyBelow' : ''}`}
            >
              <div className="emptyIcon" aria-hidden="true">🗺️</div>
              <h2>{selectionTitle(selectedDistrict, selectedGridResolution)}</h2>
              <p>{selectionPrompt(selectedDistrict, selectedGridResolution)}</p>
              <ol className="emptyGuide">
                <li>
                  <span className="egStep">1</span>
                  <span className="egText">
                    <b>지도에서 격자 클릭</b>
                    그 격자의 온도·녹지·취약성과 개선 시뮬레이션이 여기에 나와요.
                  </span>
                </li>
                <li>
                  <span className="egStep">2</span>
                  <span className="egText">
                    <b>왼쪽 ‘지도 지표 선택’</b>
                    지도 색이 무엇을 뜻하는지 바꿔 볼 수 있어요.
                  </span>
                </li>
                {showWideAreaPolicyCard && (
                  <li>
                    <span className="egStep">3</span>
                    <span className="egText">
                      <b>아래 ‘정책 시나리오’</b>
                      격자를 고르지 않아도 {wideAreaPolicyLabel}에 정책을 적용해 볼 수 있어요.
                    </span>
                  </li>
                )}
              </ol>
            </div>
            {wideAreaPolicyScope && (
              <WideAreaSimulationCard
                scope={wideAreaPolicyScope}
                selectedDistrict={selectedDistrict}
                selectedDistrictCode={selectedDistrictCode}
                selectedGridResolution={selectedGridResolution}
                onBatchSimulationResult={onBatchSimulationResult}
              />
            )}
            </>
            )}
          </div>
        </section>
      </div>
    </aside>
  );
}

// ── 직접 시뮬레이션 (목업 detail-panel-mockup.html의 '직접 시뮬레이션' 카드) ──────────
// 슬라이더 5개(녹지·불투수·NDVI·알베도·공원면적)를 조절 → '시뮬레이션 적용' → 모델 재예측.
// 슬라이더 값은 목업과 같은 정수 눈금, API에는 delta로 변환해 보낸다.
// - 100m 격자: 그 격자를 그대로 재예측 → delta_c
// - 250/500m 격자: backend가 aggregate geometry의 실제 100m 셀을 선택해
//   batch 면적가중 평균(mean_delta_c)을 계산
// 기본값은 모두 0 — 격자를 클릭한 직후에는 그 격자의 현재 값이 그대로 유지되고,
// 사용자가 움직인 것만 시나리오에 반영된다.
//
// 알베도는 단조성 제약 도입(#18)으로 방향이 바로잡혀 되살렸다.
// 제약 전 기대방향 36.3%(반대 63.7%) -> 제약 후 89.8%(반대 0.0%).
//
// 주변 공원 면적(park_area_within_500m)은 여전히 뺀 상태다. 이 변수에는 제약을 걸지
// 않았고 - 걸면 모델이 변수를 아예 포기해 99.4%가 무변화가 된다 - 방향 일치율 18.1%,
// 반대 28.0%로 역방향이 그대로다. 서울 공원은 도심 고밀도 지역에 조성되는 경우가 많아
// 사실상 '도심다움'의 대리 지표가 됐다(관측 상관 +0.353). 되살리면 "공원을 늘리면
// 더워집니다"라고 안내하게 된다.
//
// tip: 각 슬라이더가 '무엇에 얼마를 더하는지'를 설명한다. %p는 절대 퍼센트포인트여서
// 녹지율 40%인 격자에 +10%p면 50%가 된다(44%가 아니다). 이 구분을 사용자가 모르면
// 결과 크기를 완전히 다르게 읽는다.
//
// max는 3,000격자 반응 측정으로 정했다. 기준은 clip이 아니라 '포화'다. clip은 그 격자가
// 학습범위 끝에 닿아 더 못 가는 정직한 제한이고 warnings로 알려주지만, 포화는 슬라이더를
// 끌어도 결과가 안 바뀌어 고장난 것처럼 보인다.
//   green  40까지 효과 증가(-1.029℃), 40->50 증가분 -0.050으로 포화 시작
//   imp    효과는 계속 크지만 30을 넘으면 격자 절반 이상이 하한(0.024)에 걸린다
//   ndvi   30에서 이미 포화 (30->40 증가분 -0.026, 40->50 -0.002)
//   alb    5에서 완전 포화 (5 이후 증가분 0.000)
// tip은 짧게. 말풍선 폭이 185px라 길면 열 줄이 넘어가 아무도 안 읽는다.
// 각 슬라이더에서 사용자가 오해하기 쉬운 딱 한 가지만 담는다.
const SIM_SLIDERS = [
  {
    key: 'green',
    label: '녹지율 늘리기',
    min: 0,
    max: 40,
    step: 1,
    def: 0,
    tip: '녹지가 덮은 면적 비율을 올려요.\n예) 25% 격자에 +10 → 35%\n(25%의 10%가 아니에요)',
    note: '공원·가로수·화단처럼 식물이 덮은 땅'
  },
  {
    key: 'imp',
    label: '불투수면 줄이기',
    min: 0,
    max: 30,
    step: 1,
    def: 0,
    tip: '물이 스미지 않는 면적을 줄여요.\n예) 40% 격자에 −10 → 30%',
    note: '아스팔트·콘크리트처럼 물이 안 스미는 땅'
  },
  {
    key: 'ndvi',
    label: '식생 활력도 높이기',
    min: 0,
    max: 30,
    step: 1,
    def: 0,
    tip: '같은 면적이라도 잎이 더 무성해지는 정도예요.\n눈금 5 = +0.05',
    note: '나무가 얼마나 건강하고 빽빽한지'
  },
  {
    key: 'alb',
    label: '표면 반사율 높이기',
    min: 0,
    max: 5,
    step: 1,
    def: 0,
    tip: '밝은 지붕·포장재로 햇빛을 더 반사시켜요.\n\n⚠️ 모델이 이 효과를 실제보다 작게 봅니다. 참고용으로만 보세요.',
    note: '쿨루프처럼 밝은 색으로 바꾸는 것'
  }
] as const;
type SimKey = (typeof SIM_SLIDERS)[number]['key'];

// 녹지↔불투수 연동(이슈 #14)의 근거. 툴팁으로 그대로 보여준다.
// 값은 서울 64,574격자에서 실측한 것이라, 바꿀 일이 생기면 재측정 후 함께 고쳐야 한다.
const COUPLE_COEF = 0.65;
const COUPLE_TIP =
  '녹지를 늘리면 그만큼 딱딱한 바닥이 줄어야 현실적이에요.\n\n' +
  '서울 격자를 분석해보니 녹지 10만큼 늘 때 불투수면은 6.5만큼 줄었어요. ' +
  '나머지는 흙·물에서 옵니다.';
const NO_COUPLE_TIP =
  '녹지와 바닥을 따로 조절합니다.\n\n' +
  '녹지만 늘리면 현실에 없는 조합이라 효과가 실제보다 작게 나와요. ' +
  '한 가지 영향만 따로 볼 때 쓰세요.';

function simDisplay(key: SimKey, v: number): string {
  if (key === 'green') return `+${v}%p`;
  if (key === 'imp') return `−${v.toFixed(v % 1 === 0 ? 0 : 1)}%p`;
  return `+${(v / 100).toFixed(2)}`; // ndvi, albedo
}

// 슬라이더 원값(정수) → 모델 changes 델타.
// couple=true면 impervious_ratio를 아예 보내지 않는다. 보내면 백엔드가 '사용자가 직접
// 지정했다'고 보고 연동을 건너뛰기 때문이다(연동 규칙: dst in changes면 연동 안 함).
function simChanges(v: Record<SimKey, number>, couple: boolean): Record<string, number> {
  const c: Record<string, number> = {};
  if (v.green) c.green_ratio = v.green / 100;
  if (!couple && v.imp) c.impervious_ratio = -v.imp / 100;
  if (v.ndvi) c.ndvi = v.ndvi / 100;
  if (v.alb) c.albedo = v.alb / 100;
  return c;
}

// 서버는 학습범위를 벗어나는 요청을 경계값으로 clip한다. 격자 60개 표본에서 녹지 15%,
// 불투수 20%가 걸렸다. 특히 녹지↔불투수 연동은 clip을 조용히 삼켜서, 요청 +40%p든
// +5%p든 불투수가 하한(-1.4%p)에 고정되는데도 clip 경고가 뜨지 않는다.
// 그래서 요청량이 아니라 서버가 돌려준 실제 변화량(changed_features)을 표시한다.
const APPLIED_LABEL: Record<string, string> = {
  green_ratio: '녹지율',
  impervious_ratio: '불투수면',
  ndvi: '식생 활력도',
  albedo: '표면 반사율'
};

// 서버 응답의 changed_features는 모델 피처 순서라 슬라이더 순서와 다르다. 방금 움직인
// 슬라이더와 결과 줄 순서가 어긋나면 눈으로 짝을 맞추는 품이 든다. SIM_SLIDERS에서
// 순서를 끌어와 슬라이더를 재배치해도 자동으로 따라가게 한다.
const SLIDER_FEATURE: Record<SimKey, string> = {
  green: 'green_ratio',
  imp: 'impervious_ratio',
  ndvi: 'ndvi',
  alb: 'albedo'
};
const APPLIED_ORDER = SIM_SLIDERS.map((slider) => SLIDER_FEATURE[slider.key]);

const APPLIED_TIP =
  '슬라이더로 요청한 값이 아니라, 모델에 실제로 들어간 값이에요.\n\n' +
  '모델은 서울 격자에서 관측된 범위(예: 불투수면 2.4~73.4%) 안에서만 학습했어요. ' +
  '그 밖의 값은 학습한 근거가 없어 예측할 수 없으므로 경계에서 멈춥니다.\n\n' +
  '그래서 요청량과 실제 적용량이 다를 수 있고, 다르면 아래 초록 안내에 이유가 나와요.';

function describeApplied(changed?: Record<string, SimulationChangedFeature>): string[] {
  if (!changed) return [];
  // 슬라이더에 없는 피처(연동으로 따라 움직인 것 등)는 뒤로 보낸다.
  const rank = (feature: string) => {
    const index = APPLIED_ORDER.indexOf(feature);
    return index < 0 ? APPLIED_ORDER.length : index;
  };
  return Object.entries(changed)
    .sort(([a], [b]) => rank(a) - rank(b))
    .map(([feature, ba]) => {
      const diff = ba.after - ba.before;
      const label = APPLIED_LABEL[feature] ?? feature;
      // 녹지·불투수는 면적 비율이라 %p로, NDVI·albedo는 무단위 지수라 원값으로 읽힌다.
      const isRatio = feature === 'green_ratio' || feature === 'impervious_ratio';
      const amount = isRatio ? `${(diff * 100).toFixed(1)}%p` : diff.toFixed(3);
      return `${label} ${diff > 0 ? '+' : ''}${amount}`;
    });
}

// ── 슬라이더 한계 표시 ────────────────────────────────────────────────────────
// 지금까지는 '적용'을 눌러야 요청량이 잘렸는지 알 수 있었다. 학습범위는 /api/features가
// 이미 min/max로 주므로(백엔드 변경 불필요) 끌기 전에 한계를 트랙에 칠해 보여준다.
// 앱 수명 동안 안 바뀌는 값이라 모듈에 한 번만 캐시한다.
let featureRangesCache: Record<string, FeatureRange> | null = null;
let featureRangesPromise: Promise<Record<string, FeatureRange>> | null = null;

function loadFeatureRanges(): Promise<Record<string, FeatureRange>> {
  if (featureRangesCache) return Promise.resolve(featureRangesCache);
  if (!featureRangesPromise) {
    featureRangesPromise = getAiFeatureCatalog()
      .then((res) => {
        const out: Record<string, FeatureRange> = {};
        for (const item of res.features) {
          if (typeof item.min === 'number' && typeof item.max === 'number') {
            out[item.name] = { min: item.min, max: item.max };
          }
        }
        featureRangesCache = out;
        return out;
      })
      .catch(() => {
        featureRangesPromise = null;   // 다음 격자 선택 때 다시 시도한다
        return {};
      });
  }
  return featureRangesPromise;
}

function useFeatureRanges(): Record<string, FeatureRange> | null {
  const [ranges, setRanges] = useState<Record<string, FeatureRange> | null>(featureRangesCache);
  useEffect(() => {
    if (featureRangesCache) return;
    let alive = true;
    void loadFeatureRanges().then((loaded) => {
      if (alive) setRanges(loaded);
    });
    return () => {
      alive = false;
    };
  }, []);
  return ranges;
}

/**
 * 슬라이더 눈금 단위로 '요청이 온전히 반영되는 한계'와 **무엇이 막는지**를 낸다.
 *
 * 둘을 구분하는 게 중요하다. 녹지는 자기 학습범위뿐 아니라 연동된 불투수의 여유에도 막히는데,
 * 두 경우의 의미가 정반대다.
 *   - 'own'    그 변수 자체가 더 못 간다
 *   - 'couple' 변수는 끝까지 가지만 딸려 내려가는 불투수가 먼저 바닥난다
 *
 * 예: 용산 11170_01069은 녹지 자체 여유가 43.8눈금인데 연동 허용 폭은 25.9눈금이다.
 * 녹지 +34%p를 요청하면 녹지는 +34%p 그대로 들어가고 불투수만 -16.8%p로 잘린다
 * (기대 -22.1%p). 여기서 "녹지는 +25까지만 반영된다"고 쓰면 명백한 거짓말이 된다.
 */
type ReachLimit = { ticks: number; bound: 'own' | 'couple' };

function reachableTicks(
  key: SimKey,
  sliderMax: number,
  ranges: Record<string, FeatureRange> | null,
  properties: GridAnalysisProperties,
  couple: boolean
): ReachLimit | null {
  if (!ranges) return null;
  const feature = SLIDER_FEATURE[key];
  const range = ranges[feature];
  const current = Number(properties[feature as keyof GridAnalysisProperties]);
  if (!range || !Number.isFinite(current)) return null;

  // 불투수만 내리는 방향, 나머지는 올리는 방향이다.
  const own = (key === 'imp' ? current - range.min : range.max - current) * 100;
  let ticks = own;
  let bound: 'own' | 'couple' = 'own';

  if (key === 'green' && couple) {
    const impRange = ranges.impervious_ratio;
    const impNow = Number(properties.impervious_ratio);
    if (impRange && Number.isFinite(impNow)) {
      const coupled = ((impNow - impRange.min) / COUPLE_COEF) * 100;
      if (coupled < ticks) {
        ticks = coupled;
        bound = 'couple';
      }
    }
  }
  return { ticks: Math.max(0, Math.min(sliderMax, ticks)), bound };
}

// 한계값 표기. **결과의 '실제 적용'(describeApplied)과 똑같은 반올림**을 쓴다.
// 한쪽은 내리고 한쪽은 반올림하면 같은 값이 어긋나 보인다 — 11170_00649의 녹지 여유
// 37.1514눈금이 한계선엔 +37.1%p, 적용 결과엔 +37.2%p로 나와 사용자가 모순으로 읽었다.
// 한계선은 '안전한 요청값'이 아니라 '끝까지 밀었을 때 실제로 들어가는 값'이므로 이쪽이 맞다.
// simDisplay는 정수 눈금 전용(ndvi·albedo를 2자리로 반올림)이라 여기선 안 쓴다.
function limitValue(key: SimKey, ticks: number): string {
  if (key === 'green') return `+${ticks.toFixed(1)}%p`;
  if (key === 'imp') return `−${ticks.toFixed(1)}%p`;
  return `+${(ticks / 100).toFixed(3)}`;   // ndvi·albedo는 눈금/100이 실제 델타다
}

// 한계선 아래 한 줄. 무엇이 막는지에 따라 말이 완전히 달라진다.
function limitNote(key: SimKey, limit: ReachLimit): string {
  const value = limitValue(key, limit.ticks);
  if (limit.bound === 'couple') {
    return limit.ticks < 1
      ? '이 격자는 불투수면을 더 줄일 여지가 없어요'
      : `${value}부터는 불투수면이 더 줄지 않아요`;
  }
  if (limit.ticks < 1) return '이 격자는 더 바꿀 여지가 없어요';
  return key === 'imp'
    ? `이 격자는 ${value}까지만 내릴 수 있어요`
    : `이 격자는 ${value}까지만 올릴 수 있어요`;
}

function limitTip(key: SimKey, limit: ReachLimit): string {
  if (limit.bound === 'couple') {
    return (
      '녹지율 자체는 더 올릴 수 있어요. 색이 찬 구간을 넘겨도 녹지는 요청한 만큼 그대로 들어갑니다.\n\n' +
      '멈추는 건 함께 줄어드는 불투수면이에요. 이 격자는 불투수면이 이미 학습범위 하한 근처라 ' +
      '더 내려갈 자리가 없습니다.\n\n' +
      '그래서 색 구간을 넘기면 "녹지만 늘고 바닥은 그대로"인 시나리오가 됩니다. ' +
      '실제로 얼마가 들어갔는지는 결과의 "실제 적용"에 나와요.'
    );
  }
  return (
    '색이 찬 구간까지는 요청한 만큼 그대로 들어가요.\n\n' +
    '그 너머는 모델이 학습 때 본 적 없는 값이라 경계에서 멈춥니다.\n\n' +
    '끌 수는 있고, 실제로 얼마가 들어갔는지는 결과의 "실제 적용"에 나와요.'
  );
}

// 데이터 부트스트랩 8회 재학습으로 잰 delta_c 추정오차. 이보다 작은 변화는 구분할 수 없다.
const TIE_BAND_C = 0.132;

// direction_confidence는 정책 변수에 단조성 제약(이슈 #18)이 걸린 뒤로 상수가 됐다.
// 격자 60개 × 정책 4개 = 240회 측정에서 나온 값이 1.0 아니면 null 둘뿐이고, 그 사이 값은
// 한 번도 없었다. 제약 때문에 모든 트리가 같은 방향을 내놓으니 구조적으로 1.0일 수밖에 없다.
// 따라서 '저감 가능성 100%'는 정보가 0인 상수이고, 효과가 0.004℃뿐인 알베도에도 똑같이
// 붙어 오히려 신뢰도를 오해하게 만든다. 대신 추정오차와 견줘 구분 가능한 크기인지를 말한다.
//   confidence=null은 트리 300개 중 하나도 안 움직인 경우다(알베도 격자 18%). 효과가 작은
//   것과 모델이 감지조차 못 한 것은 다른 상태라 구분해서 표시한다.
function magnitudeLabel(delta: number, confidence?: number | null): string {
  if (confidence == null) return '모델이 변화를 감지하지 못함';
  if (Math.abs(delta) < TIE_BAND_C) return `추정오차(±${TIE_BAND_C}℃) 이내 — 구분 어려움`;
  return delta < 0 ? '추정오차보다 큰 저감' : '추정오차보다 큰 상승';
}

// 소수점 3자리. 정책 개입 효과가 0.1℃ 미만인 경우가 흔해서(특히 알베도) 1자리로는
// 서로 다른 시나리오가 전부 같은 값으로 보인다.
function formatDelta(delta: number): string {
  const sign = delta > 0 ? '+' : delta < 0 ? '−' : '';
  return `${sign}${Math.abs(delta).toFixed(3)}℃`;
}

function isPolicyRatio(feature: PolicyFeature): boolean {
  return (
    feature === 'green_ratio' ||
    feature === 'impervious_ratio' ||
    feature === 'road_ratio' ||
    feature === 'building_ratio'
  );
}

function formatPolicyDelta(feature: PolicyFeature, delta: number): string {
  const sign = delta > 0 ? '+' : delta < 0 ? '−' : '';
  const value = Math.abs(delta);
  return isPolicyRatio(feature)
    ? `${sign}${(value * 100).toFixed(0)}%p`
    : `${sign}${value.toFixed(2)}`;
}

function formatPolicyFeatureValue(feature: PolicyFeature, value: number): string {
  return isPolicyRatio(feature) ? `${(value * 100).toFixed(1)}%` : value.toFixed(3);
}

/* 격자를 고르지 않은 상태의 정책 카드.
   예전에는 '서울시 전체'만 있었다(분석 지역 '전체' + 격자 크기 100/250/500m).
   격자를 고르지 않아도 지금 보고 있는 자치구 전체에는 정책을 돌릴 수 있어야 해서
   범위를 인자로 받는 형태로 넓혔다(#116). 격자 크기 '구'에서도 뜬다. */
function WideAreaSimulationCard({
  scope,
  selectedDistrict,
  selectedDistrictCode,
  selectedGridResolution,
  onBatchSimulationResult
}: {
  scope: Extract<SimulationPolicyScope, 'seoul' | 'district'>;
  selectedDistrict: string;
  selectedDistrictCode: string | null;
  selectedGridResolution: GridResolution;
  onBatchSimulationResult: (result: BatchSimulationResponse | null) => void;
}) {
  return (
    <div className="card gdpSim">
      <PolicyPresetSection
        gridId=""
        aggregateGridId={null}
        guCode={null}
        selectedDistrict={selectedDistrict}
        selectedDistrictCode={selectedDistrictCode}
        selectedGridResolution={selectedGridResolution}
        properties={null}
        featureRanges={null}
        policyScope={scope}
        onBatchSimulationResult={onBatchSimulationResult}
      />
    </div>
  );
}

function PolicyPresetSection({
  gridId,
  aggregateGridId,
  guCode,
  selectedDistrict,
  selectedDistrictCode,
  selectedGridResolution,
  properties,
  featureRanges,
  policyScope,
  onBatchSimulationResult
}: {
  gridId: string;
  aggregateGridId: string | null;
  guCode: string | null;
  selectedDistrict: string;
  selectedDistrictCode: string | null;
  selectedGridResolution: GridResolution;
  properties: GridAnalysisProperties | null;
  featureRanges: Record<string, FeatureRange> | null;
  policyScope: SimulationPolicyScope;
  onBatchSimulationResult: (result: BatchSimulationResponse | null) => void;
}) {
  const isBatchResolution =
    selectedGridResolution === '250m' || selectedGridResolution === '500m';

  /* 넓은 범위 적용 (#116)
     policyScope는 좌측 '격자 크기'에서 파생된 기본 범위다(#109). 그것만으로는
     100m를 보면서 구 전체를 돌릴 수 없어서(격자 크기를 '구'로 바꿔야 했다)
     범위 버튼을 따로 둔다. 버튼의 범위는 좌측 '분석 지역'을 따라간다 —
     자치구를 보고 있으면 그 구 전체, '전체'를 보고 있으면 서울시 전체다. */
  const wideScopeKind: 'seoul' | 'district' =
    selectedDistrict === '전체' ? 'seoul' : 'district';
  // 격자를 고르면 그 격자의 구, 아니면 분석 지역의 구. 둘 다 없으면 구 전체는 못 돈다.
  const districtScopeCode = guCode ?? selectedDistrictCode;
  const [wideScopeOn, setWideScopeOn] = useState(false);
  // 기본 범위가 이미 그 버튼과 같은 상황(격자 크기 '구' + 그 구를 보는 중, 서울 전체 카드)
  // 에서는 토글할 것이 없다. 버튼은 켜진 채로 고정한다.
  const isWideScopeDefault =
    policyScope === 'seoul' ||
    (policyScope === 'district' && wideScopeKind === 'district');
  const isWideScopeOn = isWideScopeDefault || wideScopeOn;
  const effectiveScope: SimulationPolicyScope = isWideScopeDefault
    ? policyScope
    : wideScopeOn
    ? wideScopeKind
    : policyScope;
  const isDistrictScope = effectiveScope === 'district';
  const isSeoulScope = effectiveScope === 'seoul';
  const isAggregateSelection =
    isBatchResolution && !isDistrictScope && !isSeoulScope;
  const displayResolution = isBatchResolution
    ? selectedGridResolution
    : '100m';
  const skipsSingleGridApplicability =
    isBatchResolution || isDistrictScope || isSeoulScope;
  // 분석 지역이 '전체'일 때도 격자를 고르면 '구 전체'를 돌릴 수 있다(guCode는 격자에서 온다).
  // 그때 selectedDistrict를 그대로 쓰면 "전체 전체 100m 격자"가 되므로 격자의 구 이름을 쓴다.
  const districtLabel =
    selectedDistrict !== '전체'
      ? selectedDistrict
      : typeof properties?.gu_name === 'string' && properties.gu_name.trim()
      ? properties.gu_name.trim()
      : '선택 격자의 구';
  const wideScopeLabel = wideScopeKind === 'seoul' ? '서울시' : districtLabel;
  /* ML 대상은 어느 범위든 항상 실제 100m 격자다. 바뀌는 건 결과를 어느 단위로
     보여주느냐뿐이라, 버튼에도 그 단위를 적는다. 250m를 보면서 '모든 100m
     격자가 대상'이라고만 하면 지도에 250m로 그려지는 이유가 설명되지 않는다. */
  const wideScopeUnitNote =
    selectedGridResolution === 'gu'
      ? '자치구 단위로 집계해 표시'
      : selectedGridResolution === '100m'
      ? '100m 격자 단위로 표시'
      : `${selectedGridResolution} 격자 단위로 집계해 표시`;
  // 버튼을 껐을 때 돌아갈 기본 범위를 사람 말로 적는다.
  const scopeLabel =
    selectedGridResolution === '100m'
      ? '선택한 100m 격자'
      : isBatchResolution
      ? `선택한 ${selectedGridResolution} 격자`
      : `${districtLabel} 전체`;
  const [selectedPolicyId, setSelectedPolicyId] = useState<PolicyPreset['id'] | null>(null);
  const [policyBatchResult, setPolicyBatchResult] = useState<BatchSimulationResponse | null>(null);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [policyWarnings, setPolicyWarnings] = useState<string[]>([]);
  const [policyLoading, setPolicyLoading] = useState(false);
  const requestVersionRef = useRef(0);

  const [caseModalOpen, setCaseModalOpen] = useState(false);

  // 격자가 바뀌면 이전 정책 선택과 비동기 결과를 모두 무효화한다.
  useEffect(() => {
    requestVersionRef.current += 1;
    setCaseModalOpen(false);
    setSelectedPolicyId(null);
    setPolicyBatchResult(null);
    setPolicyError(null);
    setPolicyWarnings([]);
    setPolicyLoading(false);
    onBatchSimulationResult(null);
    return () => onBatchSimulationResult(null);
  }, [
    gridId,
    guCode,
    onBatchSimulationResult,
    properties?.display_grid_id,
    selectedDistrict,
    selectedGridResolution
  ]);

  // 정책 정의는 `/api/policies`에서 온다. 도착 전에는 빈 목록이라
  // 아래 지도가 먼저 그려지고 정책 카드만 나중에 채워진다.
  const policyPresets = usePolicyPresets() ?? [];
  const selectedPolicy: PolicyPreset | null =
    policyPresets.find((preset) => preset.id === selectedPolicyId) ?? null;
  // 사례 요약이 없는 정책(백엔드에 새로 생긴 정책 등)은 버튼 자체를 감춘다.
  const selectedCase = selectedPolicy ? POLICY_CASES[selectedPolicy.id] ?? null : null;
  const selectedApplicability = selectedPolicy
    ? skipsSingleGridApplicability
      ? { applicable: true }
      : properties
      ? policyApplicability(selectedPolicy, properties, featureRanges)
      : { applicable: false, reason: '격자를 선택해 주세요.' }
    : null;

  function selectPolicy(preset: PolicyPreset) {
    requestVersionRef.current += 1;
    setCaseModalOpen(false);
    setSelectedPolicyId(preset.id);
    setPolicyBatchResult(null);
    setPolicyError(null);
    setPolicyWarnings([]);
    setPolicyLoading(false);
    onBatchSimulationResult(null);
  }


  /* 범위를 인자로 받는다. 토글 직후 곧바로 다시 실행할 때 이 함수가 렌더 시점의
     effectiveScope(아직 옛 범위)를 읽으면 방금 누른 범위와 다른 요청이 나간다. */
  /* 범위를 바꾸면 이전 결과는 다른 범위의 숫자라 그대로 두면 안 된다. 지우고,
     이미 결과를 본 뒤라면(=정책을 골라 실행까지 한 상태) 새 범위로 다시 돌린다. */
  function toggleWideScope() {
    const next = !wideScopeOn;
    const nextScope: SimulationPolicyScope = next ? wideScopeKind : policyScope;
    setWideScopeOn(next);
    requestVersionRef.current += 1;
    setPolicyBatchResult(null);
    setPolicyError(null);
    setPolicyWarnings([]);
    setPolicyLoading(false);
    onBatchSimulationResult(null);
    if (selectedPolicy && policyBatchResult) void runPolicy(nextScope);
  }

  async function runPolicy(scopeOverride?: SimulationPolicyScope) {
    if (!selectedPolicy) return;
    const scope = scopeOverride ?? effectiveScope;
    const runsSeoulScope = scope === 'seoul';
    const runsDistrictScope = scope === 'district';
    const runsAggregateSelection =
      isBatchResolution && !runsSeoulScope && !runsDistrictScope;
    const applicability = isBatchResolution || runsSeoulScope || runsDistrictScope
      ? { applicable: true }
      : properties
      ? policyApplicability(selectedPolicy, properties, featureRanges)
      : { applicable: false, reason: '격자를 선택해 주세요.' };
    if (!applicability.applicable) {
      setPolicyError(applicability.reason ?? '현재 격자에는 이 정책을 적용할 수 없습니다.');
      return;
    }

    const requestVersion = ++requestVersionRef.current;
    setPolicyLoading(true);
    setPolicyBatchResult(null);
    setPolicyError(null);
    setPolicyWarnings([]);
    onBatchSimulationResult(null);

    try {
      if (runsAggregateSelection) {
        if (!aggregateGridId) {
          setPolicyError('선택한 집계 격자를 확인할 수 없어요.');
          return;
        }
        const request = policySimulationRequest(aggregateGridId, selectedPolicy);
        const response = await simulateAggregateGridPolicy(
          selectedGridResolution,
          aggregateGridId,
          request.changes ?? {},
          request.couple_land_cover ?? false
        );
        if (requestVersionRef.current !== requestVersion) return;
        if (response.mean_delta_c == null) {
          setPolicyError('시뮬레이션할 수 있는 구성 격자가 없어요.');
          return;
        }

        const clipped = response.clipped_count ?? 0;
        if (clipped > 0) {
          const total = response.valid_count ?? response.count;
          const warnings = [
            `${total.toLocaleString()}개 중 ${clipped.toLocaleString()}개는 요청량을 다 반영하지 못함`
          ];
          const unclipped = response.mean_delta_c_unclipped;
          if (
            typeof unclipped === 'number' &&
            Math.abs(unclipped - response.mean_delta_c) >= TIE_BAND_C
          ) {
            warnings.push(`그 셀을 빼면 ${formatDelta(unclipped)}`);
          }
          setPolicyWarnings(warnings);
        }
        setPolicyBatchResult(response);
        onBatchSimulationResult(response);
        return;
      }

      const request = policySimulationRequest(gridId || 'seoul', selectedPolicy);
      const response = runsSeoulScope
        ? await simulateSeoulGridPolicy(
            request.changes ?? {},
            request.couple_land_cover ?? false,
            displayResolution
          )
        : runsDistrictScope
        ? districtScopeCode
          ? await simulateDistrictGridPolicy(
              districtScopeCode,
              request.changes ?? {},
              request.couple_land_cover ?? false,
              displayResolution
            )
          : null
        : await simulateScopedGridPolicy(
            gridId,
            scope,
            request.changes ?? {},
            request.couple_land_cover ?? false
          );
      if (!response) {
        setPolicyError('자치구를 선택한 뒤 구 전체 시뮬레이션을 실행해 주세요.');
        return;
      }
      if (requestVersionRef.current !== requestVersion) return;
      if (response.success_count === 0 || response.mean_delta_c == null) {
        setPolicyError('선택한 격자를 정책 시뮬레이션할 수 없어요.');
        return;
      }
      setPolicyBatchResult(response);
      onBatchSimulationResult(response);
      const clipped = response.clipped_count ?? 0;
      const warnings: string[] = [];
      if (clipped > 0) {
        warnings.push(
          `${response.success_count.toLocaleString()}개 중 ${clipped.toLocaleString()}개는 요청량을 다 반영하지 못함`
        );
      }
      if (response.failed_count > 0) {
        warnings.push(`${response.failed_count.toLocaleString()}개 격자는 예측하지 못함`);
      }
      setPolicyWarnings(warnings);
    } catch (requestError) {
      if (requestVersionRef.current !== requestVersion) return;
      if (requestError instanceof ApiRequestError && requestError.status === 501) {
        setPolicyError('ML 모델이 아직 연결되지 않았어요.');
      } else {
        setPolicyError('정책 시뮬레이션 요청 중 오류가 났어요. 백엔드 서버를 확인해 주세요.');
      }
    } finally {
      if (requestVersionRef.current === requestVersion) setPolicyLoading(false);
    }
  }

  const singleGridResult =
    policyBatchResult?.grid_count === 1
      ? policyBatchResult.results.find((result) => result.status === 'success')
      : null;
  const appliedFeatures = selectedPolicy && singleGridResult?.changed_features
    ? selectedPolicy.affectedFeatures.flatMap((feature) => {
        const values = singleGridResult.changed_features?.[feature];
        return values ? [{ feature, values }] : [];
      })
    : [];

  return (
    <section className="policyPresetSection" aria-labelledby="policy-preset-title">
      <div className="sec-title" id="policy-preset-title">
        정책 시나리오
        <InfoTip
          align="left"
          down
          text={
            '대상 100m 격자마다 동일한 강도의 정책을 적용해 비교합니다.\n\n' +
            '정책별 변화량은 실제 효과를 보장하는 값이 아니라 비교용 표준 시나리오입니다.'
          }
        />
      </div>
      {/* 넓은 범위 적용 버튼. 격자 크기·격자 선택 여부와 상관없이 항상 보인다.
          기본 범위가 이미 이 범위면 켜진 채 고정된다(누를 것이 없다). */}
      <button
        type="button"
        className={`policyWideScopeButton${isWideScopeOn ? ' isActive' : ''}`}
        aria-pressed={isWideScopeOn}
        disabled={isWideScopeDefault || (wideScopeKind === 'district' && !districtScopeCode)}
        onClick={toggleWideScope}
      >
        <b>
          {isWideScopeOn ? '✓ ' : '+ '}
          {wideScopeLabel} 전체 적용
          {isWideScopeOn ? ' 중' : '하기'}
        </b>
        <small>
          {isWideScopeOn
            ? `${wideScopeLabel} 전체 100m 격자가 대상 · ${wideScopeUnitNote}${
                isWideScopeDefault ? '' : ' · 다시 누르면 해제'
              }`
            : `${scopeLabel} 대신 ${wideScopeLabel} 전체에 적용 · ${wideScopeUnitNote}`}
        </small>
      </button>

      <p className="policyPresetNotice">
        {isSeoulScope
          ? '서울의 모든 100m 격자에 동일한 정책 조건을 적용하는 비교 시나리오입니다.'
          : isDistrictScope
          ? `${districtLabel} 전체 100m 격자에 동일한 정책 조건을 적용하는 비교 시나리오입니다.`
          : selectedGridResolution === '100m'
          ? '선택한 100m 격자에 정책 조건을 적용하는 비교 시나리오입니다.'
          : `선택한 ${selectedGridResolution} 격자를 구성하는 각 100m 격자에 동일한 정책 조건을 적용하는 비교 시나리오입니다.`}
      </p>

      {isSeoulScope && (
        <fieldset className="policyScopeSelector">
          <legend>정책 적용 범위</legend>
          <label style={{ gridColumn: '1 / -1' }}>
            <input
              type="radio"
              name="policy-scope-seoul"
              value="seoul"
              checked
              readOnly
            />
            <span>
              <b>서울시 전체</b>
              <small>
                {selectedGridResolution === '100m'
                  ? '서울의 모든 실제 100m 격자'
                  : `실제 100m 격자를 ${selectedGridResolution}로 집계`}
              </small>
            </span>
          </label>
          <p>
            {selectedGridResolution === '100m'
              ? '서울의 모든 실제 100m 격자에 동일 정책을 적용합니다.'
              : '서울의 모든 실제 100m 격자에 동일 정책을 적용하고 현재 해상도로 집계합니다.'}
          </p>
        </fieldset>
      )}

      <div className="policyPresetGrid">
        {policyPresets.map((preset) => {
          const applicability = isBatchResolution || isDistrictScope || isSeoulScope
            ? { applicable: true }
            : properties
            ? policyApplicability(preset, properties, featureRanges)
            : { applicable: false, reason: '격자를 선택해 주세요.' };
          const selected = preset.id === selectedPolicyId;
          return (
            <button
              type="button"
              className={`policyPresetButton${selected ? ' selected' : ''}`}
              aria-pressed={selected}
              onClick={() => selectPolicy(preset)}
              key={preset.id}
            >
              <em className="policyPresetIcon" aria-hidden="true">
                {POLICY_ICONS[preset.id] ?? '🌿'}
              </em>
              <span>{POLICY_SHORT_NAMES[preset.id] ?? preset.name}</span>
              {!applicability.applicable && <small>적용 불가</small>}
            </button>
          );
        })}
      </div>

      {selectedPolicy && selectedApplicability && (
        <div className="policyPresetDetail">
          <div className="policyPresetHeading">
            <div>
              <strong>{selectedPolicy.name}</strong>
              <span>
                {selectedPolicy.scenarioLabel.replace(
                  '100m 격자 기준 표준 시나리오',
                  '각 100m 격자에 동일 조건 적용'
                )}
              </span>
            </div>
            {selectedCase && (
              <button
                className="policyCaseOpen"
                type="button"
                onClick={() => setCaseModalOpen(true)}
              >
                정책 사례 보기
              </button>
            )}
          </div>
          <p className="policyPresetDescription">{selectedPolicy.description}</p>

          <div className="policyChangeList">
            <span className="policyListTitle">변경 조건</span>
            {selectedPolicy.affectedFeatures.map((feature) => (
              <div key={feature}>
                <span>{POLICY_FEATURE_LABELS[feature]}</span>
                <b>{formatPolicyDelta(feature, selectedPolicy.changes[feature] ?? 0)}</b>
              </div>
            ))}
          </div>

          <details className="policyAssumptions">
            <summary>시나리오 가정</summary>
            <ul>
              {selectedPolicy.assumptions.map((assumption) => (
                <li key={assumption}>{assumption}</li>
              ))}
            </ul>
          </details>
          <p className="policySourceNote">
            정책 사례는 실제 시행 사례를 보여주며, 위 변화량 자체의 근거를 뜻하지 않습니다.
          </p>

          {!selectedApplicability.applicable && (
            <p className="gdpNote policyUnavailable">{selectedApplicability.reason}</p>
          )}

          <button
            className="policyRunButton"
            type="button"
            onClick={() => void runPolicy()}
            disabled={
              !selectedApplicability.applicable ||
              policyLoading ||
              (isDistrictScope && !districtScopeCode)
            }
          >
            {policyLoading
              ? isSeoulScope
                ? '서울시 전체 정책 시뮬레이션 중…'
                : isDistrictScope
                ? '구 전체 정책 시뮬레이션 중…'
                : '정책 시뮬레이션 중…'
              : '정책 시뮬레이션 실행'}
          </button>
        </div>
      )}

      {policyBatchResult && policyBatchResult.mean_delta_c != null && (
        <div className="policyResult" aria-live="polite">
          <div className="policyResultTitle">정책 시뮬레이션 결과</div>
          <dl className="policyAnomalyRows">
            <div className="policyDeltaRow">
              <dt>평균 예상 변화</dt>
              <dd>{formatDelta(policyBatchResult.mean_delta_c)}</dd>
            </div>
            <div>
              <dt>개선 격자</dt>
              <dd>{policyBatchResult.improved_grid_count.toLocaleString()}개</dd>
            </div>
            <div>
              <dt>정책 적용 범위</dt>
              <dd>
                {policyBatchResult.target_mode === 'seoul'
                  ? '서울시 전체'
                  : policyBatchResult.target_mode === 'district'
                  ? `${districtLabel} 전체`
                  : policyBatchResult.target_mode === 'aggregate'
                  ? `선택한 ${selectedGridResolution} 격자`
                  : `선택한 ${selectedGridResolution} 격자`}
              </dd>
            </div>
            <div>
              <dt>대상 격자(100m)</dt>
              <dd>{policyBatchResult.grid_count.toLocaleString()}개</dd>
            </div>
            {(policyBatchResult.target_mode === 'seoul' ||
              policyBatchResult.target_mode === 'district') &&
              policyBatchResult.display_resolution &&
              policyBatchResult.display_resolution !== '100m' && (
                <>
                  <div>
                    <dt>표시 해상도</dt>
                    <dd>{policyBatchResult.display_resolution}</dd>
                  </div>
                  <div>
                    <dt>표시 집계 격자</dt>
                    <dd>
                      {policyBatchResult.display_grid_count === undefined
                        ? '—'
                        : `${policyBatchResult.display_grid_count.toLocaleString()}개`}
                    </dd>
                  </div>
                </>
              )}
            <div>
              <dt>변화 미미</dt>
              <dd>{policyBatchResult.unchanged_grid_count.toLocaleString()}개</dd>
            </div>
            <div>
              <dt>악화 격자</dt>
              <dd>{policyBatchResult.worsened_grid_count.toLocaleString()}개</dd>
            </div>
            {policyBatchResult.failed_count > 0 && (
              <div>
                <dt>실패</dt>
                <dd>{policyBatchResult.failed_count.toLocaleString()}개</dd>
              </div>
            )}
            {(isBatchResolution ||
              policyBatchResult.target_mode === 'district' ||
              policyBatchResult.target_mode === 'seoul') && (
              <>
                <div>
                  <dt>성공 격자</dt>
                  <dd>{policyBatchResult.success_count.toLocaleString()}개</dd>
                </div>
                <div>
                  <dt>실제 적용 면적</dt>
                  <dd>
                    {policyBatchResult.successful_area_m2 == null
                      ? '—'
                      : `${Math.round(policyBatchResult.successful_area_m2).toLocaleString()}㎡`}
                  </dd>
                </div>
              </>
            )}
          </dl>
          <p className="policyAnomalyNote">
            {isAggregateSelection
              ? '선택 영역을 구성하는 100m 격자를 개별 예측한 뒤 실제 격자 면적으로 가중해 집계한 정책 시나리오입니다.'
              : (policyBatchResult.target_mode === 'seoul' ||
                  policyBatchResult.target_mode === 'district') &&
                policyBatchResult.display_resolution !== undefined &&
                policyBatchResult.display_resolution !== '100m'
              ? `정책 계산은 100m 격자 단위로 수행되며 ${policyBatchResult.display_resolution} 지도 해상도로 면적가중 집계됩니다.`
              : policyBatchResult.aggregation === 'area_weighted'
              ? '평균 예상 변화는 각 100m 격자를 개별 예측한 뒤 실제 격자 면적으로 가중한 모델 결과입니다.'
              : '평균 예상 변화는 각 구성 100m 격자를 개별 예측한 뒤 단순 평균한 모델 결과입니다.'}
            {' '}정책 적용 후는 현재 관측 LST에 모델 예측 변화량을 반영한 시나리오이며 실제 미래 관측값이 아닙니다.
            {' '}±{policyBatchResult.no_change_threshold_c.toFixed(3)}℃ 이내는 변화 미미로 분류합니다.
          </p>
          {appliedFeatures.length > 0 && (
            <div className="policyAppliedFeatures">
              <span className="policyListTitle">모델에 실제 적용된 값</span>
              {appliedFeatures.map(({ feature, values }) => (
                <div key={feature}>
                  <span>{POLICY_FEATURE_LABELS[feature]}</span>
                  <b>
                    {formatPolicyFeatureValue(feature, values.before)} →{' '}
                    {formatPolicyFeatureValue(feature, values.after)}
                  </b>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {policyWarnings.map((warning) => (
        <p className="gdpNote sc-note" key={warning}>{warning}</p>
      ))}
      {policyError && <p className="gdpNote gdpSimError">{policyError}</p>}

      {caseModalOpen && selectedPolicy && selectedCase && (
        <PolicyCaseModal
          preset={selectedPolicy}
          policyCase={selectedCase}
          onClose={() => setCaseModalOpen(false)}
        />
      )}
    </section>
  );
}

function SimulationCard({
  properties,
  selectedDistrict,
  selectedDistrictCode,
  selectedGridResolution,
  incomplete,
  onBatchSimulationResult
}: {
  properties: GridAnalysisProperties;
  selectedDistrict: string;
  selectedDistrictCode: string | null;
  selectedGridResolution: GridResolution;
  incomplete: boolean;
  onBatchSimulationResult: (result: BatchSimulationResponse | null) => void;
}) {
  const gridId = typeof properties.grid_id === 'string' ? properties.grid_id : '';
  const aggregateGridId =
    typeof properties.display_grid_id === 'string' ? properties.display_grid_id : null;
  const isBatchResolution =
    selectedGridResolution === '250m' || selectedGridResolution === '500m';
  const guCode =
    typeof properties.gu_code === 'string' || typeof properties.gu_code === 'number'
      ? String(properties.gu_code)
      : null;
  const canSimulate = Boolean(isBatchResolution ? aggregateGridId : gridId) && !incomplete;

  const [values, setValues] = useState<Record<SimKey, number>>(
    () => Object.fromEntries(SIM_SLIDERS.map((s) => [s.key, s.def])) as Record<SimKey, number>
  );
  const [result, setResult] = useState<{
    delta: number;
    sub?: string;
    notes?: string[];
    // 서버가 실제로 바꾼 값. 학습범위 clip 때문에 요청량과 다를 수 있다.
    applied?: string[];
  } | null>(
    null
  );
  const [couple, setCouple] = useState(true);
  // 정책 적용 범위는 좌측 대시보드의 '격자 크기' 선택을 그대로 따른다.
  //   100m -> 선택한 그 격자 하나
  //   250m/500m -> 그 집계 셀을 구성하는 실제 100m 격자
  //   구 -> 그 자치구 전체
  const policyScope: SimulationPolicyScope =
    selectedGridResolution === 'gu' ? 'district' : 100;
  const featureRanges = useFeatureRanges();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 연동 중에는 불투수면이 녹지에서 파생되므로 슬라이더를 잠그고 파생값을 대신 보여준다.
  // (예전엔 슬라이더 기본값 imp=5가 항상 changes에 실려 연동이 한 번도 걸리지 않았다)
  const coupledImp = values.green * COUPLE_COEF;
  const coupleActive = couple && values.green !== 0;

  async function apply() {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const changes = simChanges(values, couple);
      if (isBatchResolution) {
        if (!aggregateGridId) {
          setError('선택한 집계 격자를 확인할 수 없어요.');
          return;
        }
        const res = await simulateAggregateGridPolicy(
          selectedGridResolution,
          aggregateGridId,
          changes,
          couple
        );
        if (res.mean_delta_c == null) {
          setError('시뮬레이션할 수 있는 구성 격자가 없어요.');
          return;
        }
        const clipped = res.clipped_count ?? 0;
        const unclipped = res.mean_delta_c_unclipped;
        const applied: string[] = [];
        if (clipped > 0) {
          const total = res.valid_count ?? res.count;
          applied.push(`${total.toLocaleString()}개 중 ${clipped.toLocaleString()}개는 요청량을 다 반영하지 못함`);
          if (typeof unclipped === 'number' && Math.abs(unclipped - res.mean_delta_c) >= TIE_BAND_C) {
            applied.push(`그 셀을 빼면 ${formatDelta(unclipped)}`);
          }
        }
        setResult({
          delta: res.mean_delta_c,
          sub: `구성 100m 셀 ${res.count.toLocaleString()}개 면적가중 평균`,
          applied
        });
      } else {
        const res = await simulateGridPolicy({
          grid_id: gridId,
          changes,
          couple_land_cover: couple
        });
        if (typeof res.error === 'string') {
          setError('선택한 격자를 시뮬레이션할 수 없어요.');
          return;
        }
        // delta_std는 오차막대로 쓰지 않는다(이슈 #17) — 실제 추정오차의 약 8배다.
        // 대신 트리들이 방향에 얼마나 동의하는지를 보여준다.
        // before/after도 3자리로 맞춘다. 1자리면 12.3 → 12.0으로 보여 사용자가 0.3을
        // 계산하는데 정작 delta는 -0.246이라 어긋나 보인다.
        const sub =
          `${res.before_anomaly.toFixed(3)}℃ → ${res.after_anomaly.toFixed(3)}℃` +
          ` · ${magnitudeLabel(res.delta_c, res.direction_confidence)}`;
        setResult({
          delta: res.delta_c,
          sub,
          notes: res.warnings,
          applied: describeApplied(res.changed_features)
        });
      }
    } catch (e) {
      if (e instanceof ApiRequestError && e.status === 501) {
        setError('ML 모델이 아직 연결되지 않았어요.');
      } else {
        setError('시뮬레이션 요청 중 오류가 났어요. 백엔드 서버가 켜져 있는지 확인해 주세요.');
      }
    } finally {
      setLoading(false);
    }
  }

  if (!canSimulate) {
    return (
      /* 이 격자 하나는 못 돌려도 구·서울 전체는 돌릴 수 있다. 정책 시나리오까지
         같이 감추면 데이터가 빈 격자를 밟았다는 이유로 넓은 범위 적용이 막힌다(#116). */
      <div className="card gdpSim">
        <PolicyPresetSection
          gridId={gridId}
          aggregateGridId={aggregateGridId}
          guCode={guCode}
          selectedDistrict={selectedDistrict}
          selectedDistrictCode={selectedDistrictCode}
          selectedGridResolution={selectedGridResolution}
          properties={properties}
          featureRanges={featureRanges}
          policyScope={policyScope}
          onBatchSimulationResult={onBatchSimulationResult}
        />
        <div className="sec-title manualSimulationTitle">직접 시뮬레이션</div>
        <p className="gdpNote">
          {incomplete
            ? '데이터가 불완전한 격자라 시뮬레이션을 제공하지 않아요.'
            : '이 격자는 구성 데이터가 없어 시뮬레이션할 수 없어요.'}
        </p>
      </div>
    );
  }

  const delta = result?.delta;

  return (
    <div className="card gdpSim" id="gdpSimCard">
      <PolicyPresetSection
        key={`${selectedGridResolution}:${gridId || properties.display_grid_id || ''}`}
        gridId={gridId}
        aggregateGridId={aggregateGridId}
        guCode={guCode}
        selectedDistrict={selectedDistrict}
        selectedDistrictCode={selectedDistrictCode}
        selectedGridResolution={selectedGridResolution}
        properties={properties}
        featureRanges={featureRanges}
        policyScope={policyScope}
        onBatchSimulationResult={onBatchSimulationResult}
      />
      <div className="sec-title manualSimulationTitle">
        직접 시뮬레이션
        <InfoTip
          align="left"
          down
          text={
            '이 격자에 정책을 적용하면 온도가 얼마나 변할지 예측해요.\n\n' +
            '검증을 통과한 4가지만 조절할 수 있습니다.'
          }
        />
      </div>
      <div className="sim-ctrl">
        {SIM_SLIDERS.map((s) => {
          // 불투수면은 연동 중이면 잠기고, 녹지에서 파생된 값을 표시한다.
          const locked = s.key === 'imp' && couple;
          const shown = locked ? Math.min(coupledImp, s.max) : values[s.key];
          // 250m·500m은 구성 셀마다 한계가 달라 하나로 그릴 수 없다. 100m에서만 표시한다.
          const limit =
            selectedGridResolution === '100m'
              ? reachableTicks(s.key, s.max, featureRanges, properties, couple)
              : null;
          // 한계를 아는 슬라이더는 항상 칠한다. 한계가 있을 때만 칠하면 '전 구간 가능'과
          // '범위를 몰라 못 칠함'이 똑같은 회색이라, 여유가 넉넉한 슬라이더가 오히려
          // 못 가는 것처럼 보인다. 전부 초록 = 전 구간 가능, 일부 초록 = 거기서 벽.
          const known = limit != null;
          const limited = known && limit.ticks < s.max - 1e-9;
          const limitPct = known ? Math.min(100, (limit.ticks / s.max) * 100) : 0;
          return (
            <div className={`sim-row${locked ? ' sim-row-locked' : ''}`} key={s.key}>
              <div className="sr-top">
                <span className="sr-lab">
                  {s.label}
                  <InfoTip text={s.tip} down />
                  {locked && (
                    <span className="sr-lock" title="녹지율에 연동되어 자동 계산됩니다">
                      자동
                    </span>
                  )}
                </span>
                <span className={`sr-val${shown > 0 ? ' sr-val-on' : ''}`}>
                  {simDisplay(s.key, shown)}
                </span>
              </div>
              <p className="sr-note">{s.note}</p>
              <input
                type="range"
                min={s.min}
                max={s.max}
                step={s.step}
                value={shown}
                disabled={locked}
                aria-label={locked ? `${s.label} (녹지율에 연동되어 자동 계산됨)` : s.label}
                onChange={(e) =>
                  setValues((cur) => ({ ...cur, [s.key]: Number(e.target.value) }))
                }
                style={
                  known
                    ? {
                        background:
                          `linear-gradient(90deg, var(--gdp-green-soft) 0 ${limitPct}%,` +
                          ` var(--gdp-sub2) ${limitPct}% 100%)`
                      }
                    : undefined
                }
              />
              {limited && limit && (
                <p className="sr-limit">
                  {limitNote(s.key, limit)}
                  <InfoTip align="right" text={limitTip(s.key, limit)} />
                </p>
              )}
            </div>
          );
        })}
      </div>

      {/* 녹지↔불투수 연동 (이슈 #14). 기본 켬이되 잠그지 않는다 — 계수 0.65는 관측값이지
          구간마다 흔들리므로, 근거를 툴팁으로 노출하고 사용자가 끌 수 있게 둔다. */}
      <div className="sim-couple">
        <label className="sc-toggle">
          <input
            type="checkbox"
            checked={couple}
            onChange={(e) => setCouple(e.target.checked)}
          />
          <span>녹지를 늘리면 바닥도 함께 줄이기</span>
        </label>
        <InfoTip text={couple ? COUPLE_TIP : NO_COUPLE_TIP} align="right" />
      </div>
      <p className="sc-state">
        {coupleActive
          ? `녹지 +${values.green} → 불투수면 −${coupledImp.toFixed(1)} 적용 예정`
          : couple
            ? '녹지를 올리면 불투수면이 따라 내려갑니다'
            : '두 값을 따로 조절합니다'}
      </p>

      <div className="sim-out">
        <span className="so-lab">
          {typeof delta === 'number' && delta < 0 ? '예상 온도 저감' : '예상 온도 변화'}
        </span>
        <span className="so-num">
          {loading ? '계산 중…' : typeof delta === 'number' ? formatDelta(delta) : '—'}
        </span>
      </div>
      {result?.sub && <p className="gdpNote">{result.sub}</p>}
      {result?.applied?.length ? (
        <p className="gdpNote sc-applied">
          <span className="sc-appliedHead">
            실제 적용
            <InfoTip text={APPLIED_TIP} align="center" />
          </span>
          {result.applied.join(' / ')}
        </p>
      ) : null}
      {result?.notes?.map((note) => (
        <p className="gdpNote sc-note" key={note}>
          {note}
        </p>
      ))}
      {error && <p className="gdpNote gdpSimError">{error}</p>}

      <button className="gdpSimBtn" type="button" onClick={apply} disabled={loading}>
        {loading ? '계산 중…' : '시뮬레이션 적용'}
      </button>

      {/* 슬라이더는 '올리는 방향, 정해진 폭'만 다룬다(불투수만 내리는 방향).
          챗봇의 run_simulation은 크기 제한이 없고(_validated_delta는 유한한 수인지만 본다)
          역방향도 받으므로, 슬라이더로 못 하는 두 가지를 정확히 짚어 안내한다. */}
      <p className="gdpNote sc-more">
        슬라이더 범위를 넘거나 반대 방향 시나리오는 AI 챗봇에 문장으로 물어보세요
        <InfoTip
          align="right"
          text={
            '슬라이더는 눈금이 정해져 있고 한 방향으로만 움직여요.\n\n' +
            '챗봇에는 폭 제한이 없고 반대 방향도 물어볼 수 있어요.\n' +
            '예) "녹지율 60%p 올리면?", "녹지를 10%p 줄이면 얼마나 더워져?"\n\n' +
            '같은 모델을 쓰므로 학습범위를 넘는 값은 여기서와 똑같이 경계에서 멈추고, ' +
            '답변에 실제 반영값이 함께 나와요.'
          }
        />
      </p>
    </div>
  );
}

// ── 다른 격자와 비교 (목업 '다른 격자와 비교' 카드) ──────────────────────────────
// 지도에서 두 번째 격자를 클릭해 고르면, 주 격자(A)와 지표를 나란히 보여주고
// '가장 큰 차이'를 콜아웃한다. 두 번째 격자 선택 흐름은 MapDashboard가 관리.
type CmpDir = 'hotHigh' | 'coolHigh' | 'neutral';
// dir=hotHigh: 값이 높을수록 더움/불리 · coolHigh: 높을수록 시원/유리 · neutral: 색 없음
// salience: '가장 큰 차이' 후보 가중치(단위 스케일 보정용). 0이면 콜아웃 후보에서 제외.
const CMP_METRICS: {
  key: keyof GridAnalysisProperties;
  label: string;
  dir: CmpDir;
  salience: number;
}[] = [
  { key: 'impervious_ratio', label: '불투수면 비율', dir: 'hotHigh', salience: 100 },
  { key: 'mean_actual_lst', label: '지표면온도', dir: 'hotHigh', salience: 10 },
  { key: 'green_ratio', label: '녹지율', dir: 'coolHigh', salience: 100 },
  { key: 'building_ratio', label: '건물 비율', dir: 'hotHigh', salience: 100 },
  { key: 'ndvi', label: '식생지수(NDVI)', dir: 'coolHigh', salience: 100 },
  { key: 'est_population', label: '추정 인구', dir: 'neutral', salience: 0 },
  { key: 'nearest_shelter_distance_m', label: '쉼터 거리', dir: 'hotHigh', salience: 0 }
];

function cmpNum(properties: GridAnalysisProperties | null, key: keyof GridAnalysisProperties) {
  const v: unknown = properties?.[key];
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

// A가 B보다 '더 나쁜(더움)' 쪽이면 'hot', '더 나은(시원)' 쪽이면 'cool', 무채색이면 ''
function cmpTone(dir: CmpDir, a: number, b: number): '' | 'hot' | 'cool' {
  if (dir === 'neutral' || a === b) return '';
  const aWorse = dir === 'hotHigh' ? a > b : a < b;
  return aWorse ? 'hot' : 'cool';
}

// 콜아웃용 차이 텍스트 (지표 단위에 맞춰 %p·℃·소수)
function cmpDiffText(key: keyof GridAnalysisProperties, diffAbs: number) {
  if (key === 'mean_actual_lst') return `${diffAbs.toFixed(1)}℃`;
  if (key === 'ndvi') return diffAbs.toFixed(2);
  // 나머지 비율 지표는 0~1 저장 → %p
  return `${Math.round(diffAbs * 100)}%p`;
}

function ComparisonCard({
  properties,
  compareProperties,
  isPickingCompare,
  onStartCompare,
  onClearCompare,
  formatValue
}: {
  properties: GridAnalysisProperties;
  compareProperties: GridAnalysisProperties | null;
  isPickingCompare: boolean;
  onStartCompare: () => void;
  onClearCompare: () => void;
  formatValue: (
    p: GridAnalysisProperties | null,
    key: keyof GridAnalysisProperties
  ) => string;
}) {
  // 비교 격자가 새로 정해지면 그 카드로 자동 스크롤 (비교값이 화면에 바로 보이게)
  const cardRef = useRef<HTMLDivElement>(null);
  const compareKey =
    compareProperties?.grid_id ?? compareProperties?.display_grid_id ?? null;
  useEffect(() => {
    if (compareKey) {
      cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [compareKey]);

  // 아직 비교 격자 없음 → 안내 + 선택 버튼(또는 '선택 중' 상태)
  if (!compareProperties) {
    return (
      <div className="card gdpCmp">
        <div className="sec-title" style={{ marginBottom: 12 }}>다른 격자와 비교</div>
        {isPickingCompare ? (
          <>
            <p className="gdpNote gdpCmpPicking">지도에서 비교할 격자를 클릭하세요.</p>
            <button className="gdpCmpBtn ghost" type="button" onClick={onClearCompare}>
              취소
            </button>
          </>
        ) : (
          <>
            <p className="gdpNote">다른 격자를 골라 지표를 나란히 비교해요.</p>
            <button className="gdpCmpBtn" type="button" onClick={onStartCompare}>
              + 비교할 격자 선택
            </button>
          </>
        )}
      </div>
    );
  }

  const bName =
    [compareProperties.gu_name, compareProperties.dong_name].filter(Boolean).join(' ') ||
    '비교 격자';
  const bId = compareProperties.display_grid_id ?? compareProperties.grid_id ?? '';

  // 각 지표의 원값을 뽑고, 콜아웃(가장 큰 차이) 후보를 계산
  const rows = CMP_METRICS.map((m) => {
    const a = cmpNum(properties, m.key);
    const b = cmpNum(compareProperties, m.key);
    const both = a !== null && b !== null;
    const score = both && m.salience > 0 ? Math.abs(a - b) * m.salience : -1;
    return { ...m, a, b, both, score };
  });

  const top = rows.reduce((best, r) => (r.score > best.score ? r : best), {
    score: -1
  } as (typeof rows)[number]);

  let callout: string | null = null;
  if (top.score > 0 && top.a !== null && top.b !== null) {
    const diff = top.a - top.b;
    const higher = diff > 0;
    callout = `${top.label}: 이 격자가 ${cmpDiffText(top.key, Math.abs(diff))} 더 ${
      higher ? '높아요' : '낮아요'
    }`;
  }

  return (
    <div className="card gdpCmp" ref={cardRef}>
      <div className="sec-title" style={{ marginBottom: 12 }}>다른 격자와 비교</div>

      <div className="cmp-sub">
        <span className="cmp-name">{bName}</span>
        {bId && <span className="cmp-id">{bId}</span>}
      </div>

      {callout && (
        <div className="cmp-hero">
          <div className="ch-lab">가장 큰 차이</div>
          <div className="ch-txt">{callout}</div>
        </div>
      )}

      <div className="cmp-hrow">
        <span>지표</span>
        <span>이 격자</span>
        <span>비교 격자</span>
      </div>
      {rows.map((r) => {
        const tone = r.both ? cmpTone(r.dir, r.a as number, r.b as number) : '';
        return (
          <div className="cmp-row" key={r.key}>
            <span className="c-lab">{r.label}</span>
            <span className={`c-mine ${tone}`}>
              {r.a !== null ? formatValue(properties, r.key) : '—'}
            </span>
            <span className="c-theirs">
              {r.b !== null ? formatValue(compareProperties, r.key) : '—'}
            </span>
          </div>
        );
      })}

      <button className="gdpCmpBtn ghost" type="button" onClick={onClearCompare}>
        비교 해제
      </button>
    </div>
  );
}

export default GridDetailSidePanel;
