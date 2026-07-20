import type { ReactNode } from 'react';
import type { GridAnalysisProperties, GridResolution } from '../types/dashboard';

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


function ReportSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="gridReportSection">
      <h3>{title}</h3>
      <dl>{children}</dl>
    </section>
  );
}

function ReportRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="gridReportRow">
      <dt>{label}</dt>
      <dd>{value}</dd>
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
  formatValue
}: Props) {
  const fmt = (key: keyof GridAnalysisProperties) => formatValue(properties, key);
  const guLabel = properties
    ? [properties.gu_name, properties.dong_name].filter(Boolean).join(' ')
    : '';
  const gridId = properties?.display_grid_id ?? properties?.grid_id ?? '';
  const lst = properties?.mean_actual_lst;
  const anomaly = properties?.mean_actual_anomaly;
  const level = heatLevel(anomaly);
  // 우선순위: 구 내부 순위(rank) + 분모(total)로 백분위(pct)를 낸다.
  // rank 1 = 가장 시급 → 상위 1%. rank가 클수록(=뒤쪽) 개선 급하지 않은 격자.
  const rank = properties?.priority_rank;
  const total = guGridTotal;
  const pct =
    rank != null && total ? Math.max(1, Math.round((rank / total) * 100)) : null;
  const buildingEstimated = properties?.building_ratio_estimated === true;
  const greenDelta = properties?.green_delta_c;   // 녹지확대 시 저감효과 (음수=냉각)
  const pop = properties?.est_population;          // 추정 거주 인구

  return (
    <aside className={isOpen ? 'gridDetailSidePanel' : 'gridDetailSidePanel collapsed'}>
      <button type="button" className="sidePanelToggle" onClick={onToggle}>
        {isOpen ? '›' : '‹'}
      </button>

      <div className="sidePanelBody">
        {properties ? (
          <div className="gridReport">
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

            <div className="tiles">
              <div className="tile">
                <div className="t-lab">녹지화 시 저감 가능</div>
                <div className={`t-num ${greenDelta != null && greenDelta < 0 ? 'cool' : ''}`}>
                  {greenDelta != null ? fmt('green_delta_c') : '—'}
                </div>
              </div>
              <div className="tile">
                <div className="t-lab">추정 거주 인구</div>
                <div className="t-num">
                  {pop != null ? `${Math.round(pop).toLocaleString()}명` : '—'}
                </div>
              </div>
            </div>

            {(properties.top1_feature || properties.top2_feature) && (
              <ReportSection title="이 격자가 뜨거운 이유 · 기여도(℃)">
                {properties.top1_feature && (
                  <ReportRow label={`1. ${properties.top1_feature}`} value={fmt('top1_shap')} />
                )}
                {properties.top2_feature && (
                  <ReportRow label={`2. ${properties.top2_feature}`} value={fmt('top2_shap')} />
                )}
                {properties.top3_feature && (
                  <ReportRow label={`3. ${properties.top3_feature}`} value={fmt('top3_shap')} />
                )}
              </ReportSection>
            )}

            <ReportSection title="환경">
              <ReportRow label="녹지율" value={fmt('green_ratio')} />
              <ReportRow label="식생지수 (NDVI)" value={fmt('ndvi')} />
              <ReportRow
                label={buildingEstimated ? '건물 비율 (추정)' : '건물 비율'}
                value={`${buildingEstimated ? '≈ ' : ''}${fmt('building_ratio')}`}
              />
              <ReportRow label="불투수면 비율" value={fmt('impervious_ratio')} />
            </ReportSection>
            {buildingEstimated && (
              <p className="gdpNote">
                * 이 격자는 건물 도형(VWorld) 데이터가 없어, 위성 지표면(built-up)으로
                건물 비율을 추정한 값입니다.
              </p>
            )}

            <ReportSection title="취약성">
              <ReportRow label="추정 인구 (명)" value={fmt('est_population')} />
              <ReportRow label="추정 고령인구 (명, 65+)" value={fmt('est_elderly')} />
              <ReportRow label="가장 가까운 쉼터" value={fmt('nearest_shelter_distance_m')} />
            </ReportSection>
          </div>
        ) : (
          <div className="gridReportEmpty">
            <h2>상세 페이지</h2>
            <p>{selectionPrompt(selectedDistrict, selectedGridResolution)}</p>
          </div>
        )}
      </div>
    </aside>
  );
}

export default GridDetailSidePanel;
