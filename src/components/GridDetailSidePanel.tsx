import { useEffect, useRef, useState } from 'react';
import {
  ApiRequestError,
  getAiFeatureCatalog,
  simulateBatchGridPolicy,
  simulateGridPolicy
} from '../services/api';
import type {
  GridAnalysisProperties,
  GridResolution,
  SimulationChangedFeature,
  SimulationResponse
} from '../types/dashboard';

interface Props {
  properties: GridAnalysisProperties | null;
  // 선택 격자가 속한 구의 전체 격자 수 (priority_rank의 분모). 없으면 순위만 표시.
  guGridTotal: number | null;
  selectedDistrict: string;
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
function isIncompleteGrid(properties: GridAnalysisProperties | null): boolean {
  if (!properties) return false;
  return CORE_MODEL_FEATURES.some((key) => {
    const v = properties[key];
    return v == null || (typeof v === 'number' && !Number.isFinite(v));
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

function selectionPrompt(district: string, resolution: GridResolution) {
  if (district === '전체' && resolution === '100m') {
    return '서울 전체에서 분석할 격자를 클릭하세요.';
  }
  if (district === '전체') {
    return '지도에서 분석할 지역을 클릭하세요.';
  }
  return `${district}에서 분석할 격자를 클릭하세요.`;
}

function GridDetailSidePanel({
  properties,
  guGridTotal,
  selectedDistrict,
  selectedGridResolution,
  isOpen,
  onToggle,
  formatValue,
  compareProperties,
  isPickingCompare,
  onStartCompare,
  onClearCompare
}: Props) {
  const fmt = (key: keyof GridAnalysisProperties) => formatValue(properties, key);
  const guLabel = properties
    ? [properties.gu_name, properties.dong_name].filter(Boolean).join(' ')
    : '';
  const gridId = properties?.display_grid_id ?? properties?.grid_id ?? '';
  // AI 채팅 문맥(selectedGridId 등) 계산은 MapDashboard로 옮겼다(#26).
  const lst = properties?.mean_actual_lst;
  const anomaly = properties?.mean_actual_anomaly;
  const level = heatLevel(anomaly);
  // 핵심 피처 결측 격자면 모델 기반 분석(SHAP·시뮬레이션)을 신뢰불가로 처리한다.
  const incomplete = isIncompleteGrid(properties);
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
    '폭염 때 이용할 수 있는 냉방 대피 시설이에요. 가까울수록 취약계층 대응에 유리합니다.';

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
      <button type="button" className="sidePanelToggle" onClick={onToggle}>
        {isOpen ? '›' : '‹'}
      </button>

      <div className="sidePanelBody">
        <section className="rightPanelDashboard">
          <div className="gridReport">
            {properties ? (
            <>
            <div className="card">
              <div className="eyebrow">격자 상세</div>
              <div className="gu">{guLabel || '선택 격자'}</div>
              <div className="id">{gridId} · 면적 {fmt('area_m2')}</div>

              <div className="hero-main">
                <div>
                  <div className={`ht-num ${level.tone}`}>
                    {lst != null ? lst.toFixed(1) : '—'}<span>℃</span>
                  </div>
                  <div className="ht-sub">
                    지표면온도 · 구 평균보다{' '}
                    <b className={level.tone}>{anomaly != null ? `${anomaly > 0 ? '+' : ''}${anomaly.toFixed(1)}℃` : '—'}</b>{' '}
                    {anomaly != null && anomaly >= 0 ? '높음' : '낮음'}
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
            </div>

            {incomplete && (
              <div className="card gdpIncomplete">
                <div className="sec-title">⚠ 데이터 불완전 격자</div>
                <p className="gdpNote">
                  이 격자는 위성·지형 데이터 일부가 없어 모델이 온전히 분석할 수 없어요.
                  온도 영향 요인(TOP)과 시뮬레이션은 신뢰도가 낮아 제공하지 않습니다.
                  (서울 전체의 약 0.05% · 대부분 한강 수면·경계 격자)
                </p>
              </div>
            )}

            <div className="tiles">
              <div className="tile">
                <div className="t-lab">
                  녹지화 시 저감 가능
                  <InfoTip text="녹지율 +5%p · NDVI +0.03 · 불투수면 −5%p 를 함께 적용했을 때 모델이 예측한 온도 변화입니다. 모든 격자에 같은 조건을 적용한 값이라, 격자끼리 '녹지화 여지'를 비교하는 용도예요. 아래 직접 시뮬레이션은 원하는 값을 넣어보는 것이라 결과가 다를 수 있습니다." />
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
                            {METRIC_DESC[it.f] && <InfoTip text={METRIC_DESC[it.f]} />}
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
                  tip={METRIC_DESC.green_ratio}
                  tipAlign="left"
                />
                <Donut
                  value={properties.impervious_ratio}
                  color="var(--gdp-hot)"
                  label="불투수면"
                  tip={METRIC_DESC.impervious_ratio}
                  tipAlign="center"
                />
                <Donut
                  value={properties.building_ratio}
                  color="var(--gdp-ink2)"
                  label="건물"
                  tip={METRIC_DESC.building_ratio}
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
                  <span className="r-lab">추정 거주 인구</span>
                  <span className="r-val">
                    {popUnavailable
                      ? '집계 제외'
                      : pop != null
                      ? `${Math.round(pop).toLocaleString()}명`
                      : '—'}
                  </span>
                </div>
                <div className="row">
                  <span className="r-lab">추정 고령인구 (65+)</span>
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
              selectedGridResolution={selectedGridResolution}
              incomplete={incomplete}
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
            <div className="gridReportEmpty">
              <div className="emptyIcon" aria-hidden="true">🗺️</div>
              <h2>격자를 선택해 주세요</h2>
              <p>{selectionPrompt(selectedDistrict, selectedGridResolution)}</p>
              <p className="emptyHint">
                격자를 클릭하면 온도·녹지·취약성과 개선 시뮬레이션을 볼 수 있어요.
              </p>
            </div>
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
// - 250/500m 격자: 구성 100m 셀들(member_grid_ids)에 같은 정책을 적용해 batch 평균(mean_delta_c)
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

// 구별 250m 파일 일부는 member_grid_ids가 배열이 아니라 numpy 배열을 그대로 문자열화한
// 형태로 들어 있다 — "['11560_00004' '11560_00005']" 처럼 쉼표 없이 공백으로만 구분된다.
// 서울 전체 파일(seoul_grid_250m/500m.geojson)은 정상 배열이라, '분석지역=전체'에서는
// 시뮬레이션이 되고 구를 고르면 통째로 막혔다(Array.isArray가 false → 대상 0개).
// 데이터를 다시 굽는 게 근본 해결이지만, 이미 배포된 파일도 읽히게 둔다.
const GRID_ID_TOKEN = /\d{5}_\d{5}/g;

function readMemberIds(raw: unknown): string[] {
  if (Array.isArray(raw)) return raw.filter((id): id is string => typeof id === 'string');
  if (typeof raw === 'string') return raw.match(GRID_ID_TOKEN) ?? [];
  return [];
}

// ── 슬라이더 한계 표시 ────────────────────────────────────────────────────────
// 지금까지는 '적용'을 눌러야 요청량이 잘렸는지 알 수 있었다. 학습범위는 /api/features가
// 이미 min/max로 주므로(백엔드 변경 불필요) 끌기 전에 한계를 트랙에 칠해 보여준다.
// 앱 수명 동안 안 바뀌는 값이라 모듈에 한 번만 캐시한다.
type FeatureRange = { min: number; max: number };
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

function SimulationCard({
  properties,
  selectedGridResolution,
  incomplete
}: {
  properties: GridAnalysisProperties;
  selectedGridResolution: GridResolution;
  incomplete: boolean;
}) {
  const gridId = typeof properties.grid_id === 'string' ? properties.grid_id : '';
  const memberIds = readMemberIds(properties.member_grid_ids);
  // 재예측 대상 100m 격자들: 100m면 자기 자신, 250/500m면 구성 100m 셀들
  const targetIds = selectedGridResolution === '100m' ? (gridId ? [gridId] : []) : memberIds;
  const canSimulate = targetIds.length > 0 && !incomplete;

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
      if (targetIds.length === 1) {
        const res = await simulateGridPolicy({
          grid_id: targetIds[0],
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
      } else {
        const res = await simulateBatchGridPolicy(targetIds, changes, couple);
        if (res.mean_delta_c == null) {
          setError('시뮬레이션할 수 있는 구성 격자가 없어요.');
          return;
        }
        // 구성 셀 중 일부는 학습범위 clip에 걸려 요청량을 다 못 받는다. 그 셀들은 개입이
        // 작은데도 저감이 크게 나와 평균을 끌어내리므로(구별 200셀 표본에서 0.13~0.35℃,
        // 추정오차 0.132℃보다 크다) 몇 개가 잘렸는지와 뺀 평균을 함께 알린다.
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
          sub: `구성 100m 셀 ${res.count.toLocaleString()}개 평균`,
          applied
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
      <div className="card gdpSim">
        <div className="sec-title">직접 시뮬레이션</div>
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
    <div className="card gdpSim">
      <div className="sec-title">
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
