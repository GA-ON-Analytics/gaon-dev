import type { ReactNode } from 'react';
import type { GridAnalysisProperties, GridResolution } from '../types/dashboard';

interface Props {
  properties: GridAnalysisProperties | null;
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

  return (
    <aside className={isOpen ? 'gridDetailSidePanel' : 'gridDetailSidePanel collapsed'}>
      <button type="button" className="sidePanelToggle" onClick={onToggle}>
        {isOpen ? '›' : '‹'}
      </button>

      <div className="sidePanelBody">
        {properties ? (
          <div className="gridReport">
            <header className="gridReportHead">
              <div className="gridReportGu">{guLabel || '선택 격자'}</div>
              <div className="gridReportId">
                {gridId} · {fmt('area_m2')}
              </div>
              <div className="gridReportPriority">
                <strong>{fmt('priority_score')}</strong>
                <span>개선 우선순위 · 구내 {fmt('priority_rank')}위</span>
              </div>
            </header>

            <ReportSection title="열 상태">
              <ReportRow label="실제 지표면온도" value={fmt('mean_actual_lst')} />
              <ReportRow label="현재 열 위험 (구 평균 대비)" value={fmt('mean_actual_anomaly')} />
              <ReportRow label="녹지 확대 시 저감효과 (음수=냉각)" value={fmt('green_delta_c')} />
            </ReportSection>

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
              <ReportRow label="건물 비율" value={fmt('building_ratio')} />
              <ReportRow label="불투수면 비율" value={fmt('impervious_ratio')} />
            </ReportSection>

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
