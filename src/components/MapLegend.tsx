import type { GridAnalysisProperties, LayerKey } from '../types/dashboard';

// 지도 색 범례 — 가로 램프형.
//
// ★ 색을 여기서 다시 적지 않는다.
// 각 구간의 대표값(probe)을 실제 채색 함수에 넣어 색을 '되받아' 온다.
// colorByLayerValue의 색을 바꾸면 범례가 저절로 따라오고,
// 범례와 지도가 다른 색을 말하는 사고가 구조적으로 불가능해진다.
// (임계값 자체를 바꿀 때는 아래 ticks도 함께 고쳐야 한다)

export interface MapLegendProps {
  layer: LayerKey;
  layerLabel: string;
  /** MapDashboard의 colorByLayerValue를 그대로 주입받는다 */
  colorOf: (properties: GridAnalysisProperties, layer: LayerKey) => string;
  neutralColor: string;
  /**
   * 구 단위 화면이면 사분위 경계를 받는다.
   * 이때는 고정 임계값 대신 '지금 화면의 구들' 안에서의 상대 위치를 보여준다.
   */
  quantile?: { breaks: number[]; ramp: string[]; count: number } | null;
}

interface LegendSpec {
  hint: string;
  /** 낮은 값 → 높은 값 순서. 가로 램프의 왼쪽이 낮은 값이다. */
  probes: number[];
  /** 구간 경계값. probes보다 하나 적다 (4구간 → 경계 3개) */
  ticks: string[];
  /** 램프 양 끝에 붙는 방향 표시 */
  lowLabel: string;
  highLabel: string;
}

const SPECS: Partial<Record<LayerKey, LegendSpec>> = {
  priority_score: {
    hint: '종합 점수 0~100',
    probes: [20, 50, 70, 90],
    ticks: ['40', '60', '80'],
    lowLabel: '여유',
    highLabel: '시급'
  },
  mean_actual_anomaly: {
    hint: '구 평균 대비 온도차',
    probes: [-1, 0.5, 2, 4],
    ticks: ['0', '+1.5', '+3.0℃'],
    lowLabel: '서늘',
    highLabel: '더움'
  },
  mean_actual_lst: {
    hint: '위성 관측 지표면온도',
    probes: [30, 36, 39, 42],
    ticks: ['35', '38', '41℃'],
    lowLabel: '낮음',
    highLabel: '높음'
  },
  green_ratio: {
    hint: '식생이 덮은 비율',
    probes: [0.05, 0.15, 0.28, 0.4],
    ticks: ['12', '22', '35%'],
    lowLabel: '적음',
    highLabel: '많음'
  },
  ndvi: {
    hint: '식생 활력 지수',
    probes: [0.05, 0.15, 0.28, 0.4],
    ticks: ['0.12', '0.22', '0.35'],
    lowLabel: '낮음',
    highLabel: '높음'
  },
  building_ratio: {
    hint: '건물이 덮은 비율',
    probes: [0.1, 0.4, 0.6, 0.8],
    ticks: ['30', '50', '70%'],
    lowLabel: '적음',
    highLabel: '많음'
  },
  impervious_ratio: {
    hint: '아스팔트·콘크리트 비율',
    probes: [0.1, 0.4, 0.6, 0.8],
    ticks: ['30', '50', '70%'],
    lowLabel: '적음',
    highLabel: '많음'
  },
  nearest_shelter_distance_m: {
    hint: '가장 가까운 쉼터 거리',
    probes: [100, 400, 800, 1500],
    ticks: ['250', '500', '1km'],
    lowLabel: '가까움',
    highLabel: '멂'
  }
};

/* 구 단위에서 의미가 달라지는 지표는 설명 문구도 바꾼다.
   현재 열 위험은 격자에서 '구 평균 대비'지만 구 단위에서는 '서울 평균 대비'다
   (구 평균 대비 값을 구 전체로 평균하면 정의상 0이 되므로 다른 필드를 쓴다). */
const DISTRICT_HINT: Partial<Record<LayerKey, string>> = {
  mean_actual_anomaly: '서울 평균 대비 온도차',
  priority_score: '서울 25개 구 기준 종합 점수'
};

/** 사분위 경계값을 사람이 읽는 문자열로. 지표마다 단위가 다르다. */
function formatBreak(layer: LayerKey, value: number) {
  switch (layer) {
    case 'green_ratio':
    case 'building_ratio':
    case 'impervious_ratio':
      return `${(value * 100).toFixed(0)}%`;
    case 'ndvi':
      return value.toFixed(2);
    case 'mean_actual_anomaly':
      return `${value >= 0 ? '+' : ''}${value.toFixed(1)}`;
    case 'mean_actual_lst':
      return value.toFixed(1);
    case 'nearest_shelter_distance_m':
      return value >= 1000 ? `${(value / 1000).toFixed(1)}km` : `${Math.round(value)}m`;
    default:
      return value.toFixed(0);
  }
}

export default function MapLegend({
  layer,
  layerLabel,
  colorOf,
  neutralColor,
  quantile
}: MapLegendProps) {
  const spec = SPECS[layer];
  if (!spec) return null;

  // 구 단위: 화면에 있는 구들에서 뽑은 사분위. 격자 단위: 고정 임계값.
  const isQuantile = Boolean(quantile);
  const colors = quantile
    ? quantile.ramp
    : spec.probes.map((probe) =>
        colorOf({ [layer]: probe } as GridAnalysisProperties, layer)
      );
  const ticks = quantile
    ? quantile.breaks.map((value) => formatBreak(layer, value))
    : spec.ticks;
  const baseHint = (quantile && DISTRICT_HINT[layer]) || spec.hint;
  const hint = quantile ? `${baseHint} · 구 ${quantile.count}개 사분위` : spec.hint;

  return (
    <section className="mapLegend" aria-label={`${layerLabel} 색상 범례`}>
      <header className="mapLegendHead">
        <strong>{layerLabel}</strong>
        <span>{hint}</span>
        {!isQuantile && (
          <span className="mapLegendEmpty">
            <i style={{ background: neutralColor }} aria-hidden="true" />
            없음
          </span>
        )}
      </header>

      <div className="mapLegendRamp" role="img" aria-label={ticks.join(', ')}>
        {colors.map((color, index) => (
          <span key={`${color}-${index}`} style={{ background: color }} />
        ))}
      </div>

      {/* 경계값은 구간이 나뉘는 지점(25% / 50% / 75%) 위에 올리고,
          방향 표시(적음/많음)는 양 끝에 붙여 한 줄로 합쳤다. */}
      <div className="mapLegendTicks">
        <span className="mapLegendEnd isLow">{spec.lowLabel}</span>
        {ticks.map((tick, index) => (
          <span key={`${tick}-${index}`} style={{ left: `${((index + 1) * 100) / colors.length}%` }}>
            {tick}
          </span>
        ))}
        <span className="mapLegendEnd isHigh">{spec.highLabel}</span>
      </div>
    </section>
  );
}
