import { useEffect, useRef, useState } from 'react';
import { ApiRequestError, simulateBatchGridPolicy, simulateGridPolicy } from '../services/api';
import type { GridAnalysisProperties, GridResolution, SimulationResponse } from '../types/dashboard';
import AiChatView from './AiChatView';

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
  built_surface_ratio: '지표면(built-up)',
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
  'built_surface_ratio',
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
  built_surface_ratio: '위성으로 본 인공 지표면(건물+도로+포장) 비율.',
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
function InfoTip({ text, align = 'left' }: { text: string; align?: 'left' | 'center' | 'right' }) {
  return (
    <span className={`infotip infotip-${align}`} tabIndex={0} aria-label={text}>
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
  const [rightPanelMode, setRightPanelMode] = useState<'dashboard' | 'chat'>('dashboard');
  const fmt = (key: keyof GridAnalysisProperties) => formatValue(properties, key);
  const guLabel = properties
    ? [properties.gu_name, properties.dong_name].filter(Boolean).join(' ')
    : '';
  const gridId = properties?.display_grid_id ?? properties?.grid_id ?? '';
  // AI Tool 문맥에는 ML 데이터셋의 실제 100m grid_id만 전달한다.
  // display_grid_id는 헤더 표시용이며 API 문맥으로 승격하지 않는다.
  const selectedGridId =
    selectedGridResolution === '100m' &&
    typeof properties?.grid_id === 'string' &&
    properties.grid_id.trim()
      ? properties.grid_id.trim()
      : null;
  const selectedDisplayGridId = gridId || null;
  const selectedGuName =
    typeof properties?.gu_name === 'string' && properties.gu_name.trim()
      ? properties.gu_name.trim()
      : null;
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
    <aside className={isOpen ? 'gridDetailSidePanel' : 'gridDetailSidePanel collapsed'}>
      <button type="button" className="sidePanelToggle" onClick={onToggle}>
        {isOpen ? '›' : '‹'}
      </button>

      <div className="sidePanelBody">
        <section
          className="rightPanelDashboard"
          hidden={rightPanelMode !== 'dashboard'}
          aria-hidden={rightPanelMode !== 'dashboard'}
        >
          <button
            type="button"
            className="gaonAiEntry"
            onClick={() => setRightPanelMode('chat')}
          >
            <span className="gaonAiEntryIcon" aria-hidden="true">AI</span>
            <span className="gaonAiEntryText">
              <strong>GA:ON AI</strong>
              <small>선택 격자를 질문하고 정책 변화를 살펴보세요</small>
            </span>
            <span className="gaonAiEntryArrow" aria-hidden="true">›</span>
          </button>

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
                  <InfoTip text="녹지율 +5%p · NDVI +0.03 · 불투수/지표면 각 −5%p 를 적용했을 때 모델이 예측한 온도 변화입니다. 격자별 '녹지화 여지'를 비교하는 지표예요." />
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

        <section
          className="rightPanelChat"
          hidden={rightPanelMode !== 'chat'}
          aria-hidden={rightPanelMode !== 'chat'}
        >
          <AiChatView
            isActive={rightPanelMode === 'chat' && isOpen}
            selectedGridId={selectedGridId}
            selectedDisplayGridId={selectedDisplayGridId}
            selectedGuName={selectedGuName}
            onBack={() => setRightPanelMode('dashboard')}
          />
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
const SIM_SLIDERS = [
  { key: 'green', label: '녹지율 늘리기', min: 0, max: 20, step: 1, def: 5 },
  { key: 'imp', label: '불투수면 줄이기', min: 0, max: 20, step: 1, def: 5 },
  { key: 'ndvi', label: '식생지수(NDVI) 늘리기', min: 0, max: 30, step: 1, def: 5 },
  { key: 'alb', label: '알베도(반사율) 늘리기', min: 0, max: 20, step: 1, def: 5 },
  { key: 'park', label: '주변 공원 면적 확충', min: 0, max: 5000, step: 250, def: 1000 }
] as const;
type SimKey = (typeof SIM_SLIDERS)[number]['key'];

function simDisplay(key: SimKey, v: number): string {
  if (key === 'green') return `+${v}%p`;
  if (key === 'imp') return `−${v}%p`;
  if (key === 'ndvi' || key === 'alb') return `+${(v / 100).toFixed(2)}`;
  return `+${v.toLocaleString()}㎡`;
}

// 슬라이더 원값(정수) → 모델 changes 델타
function simChanges(v: Record<SimKey, number>): Record<string, number> {
  const c: Record<string, number> = {};
  if (v.green) c.green_ratio = v.green / 100;
  if (v.imp) c.impervious_ratio = -v.imp / 100;
  if (v.ndvi) c.ndvi = v.ndvi / 100;
  if (v.alb) c.albedo = v.alb / 100;
  if (v.park) c.park_area_within_500m = v.park;
  return c;
}

function formatDelta(delta: number): string {
  const sign = delta > 0 ? '+' : delta < 0 ? '−' : '';
  return `${sign}${Math.abs(delta).toFixed(1)}℃`;
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
  const memberIds = Array.isArray(properties.member_grid_ids)
    ? (properties.member_grid_ids.filter((id): id is string => typeof id === 'string'))
    : [];
  // 재예측 대상 100m 격자들: 100m면 자기 자신, 250/500m면 구성 100m 셀들
  const targetIds = selectedGridResolution === '100m' ? (gridId ? [gridId] : []) : memberIds;
  const canSimulate = targetIds.length > 0 && !incomplete;

  const [values, setValues] = useState<Record<SimKey, number>>(
    () => Object.fromEntries(SIM_SLIDERS.map((s) => [s.key, s.def])) as Record<SimKey, number>
  );
  const [result, setResult] = useState<{ delta: number; sub?: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function apply() {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const changes = simChanges(values);
      if (targetIds.length === 1) {
        const res = await simulateGridPolicy({ grid_id: targetIds[0], changes });
        if (typeof res.error === 'string') {
          setError('선택한 격자를 시뮬레이션할 수 없어요.');
          return;
        }
        const sub =
          typeof res.delta_std === 'number'
            ? `${res.before_anomaly.toFixed(1)}℃ → ${res.after_anomaly.toFixed(1)}℃ · 트리 간 변화량 편차 ${res.delta_std.toFixed(2)}℃`
            : undefined;
        setResult({ delta: res.delta_c, sub });
      } else {
        const res = await simulateBatchGridPolicy(targetIds, changes);
        if (res.mean_delta_c == null) {
          setError('시뮬레이션할 수 있는 구성 격자가 없어요.');
          return;
        }
        setResult({ delta: res.mean_delta_c, sub: `구성 100m 셀 ${res.count.toLocaleString()}개 평균` });
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
      <div className="sec-title">직접 시뮬레이션</div>
      <div className="sim-ctrl">
        {SIM_SLIDERS.map((s) => (
          <div className="sim-row" key={s.key}>
            <div className="sr-top">
              <span className="sr-lab">{s.label}</span>
              <span className="sr-val">{simDisplay(s.key, values[s.key])}</span>
            </div>
            <input
              type="range"
              min={s.min}
              max={s.max}
              step={s.step}
              value={values[s.key]}
              onChange={(e) =>
                setValues((cur) => ({ ...cur, [s.key]: Number(e.target.value) }))
              }
            />
          </div>
        ))}
      </div>

      <div className="sim-out">
        <span className="so-lab">
          {typeof delta === 'number' && delta < 0 ? '예상 온도 저감' : '예상 온도 변화'}
        </span>
        <span className="so-num">
          {loading ? '계산 중…' : typeof delta === 'number' ? formatDelta(delta) : '—'}
        </span>
      </div>
      {result?.sub && <p className="gdpNote">{result.sub}</p>}
      {error && <p className="gdpNote gdpSimError">{error}</p>}

      <button className="gdpSimBtn" type="button" onClick={apply} disabled={loading}>
        {loading ? '계산 중…' : '시뮬레이션 적용'}
      </button>
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
