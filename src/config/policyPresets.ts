import type {
  FeatureRange,
  GridAnalysisProperties,
  PolicyApplicability,
  PolicyFeature,
  PolicyPreset,
  SimulationRequest
} from '../types/dashboard';

export const POLICY_FEATURE_LABELS: Record<PolicyFeature, string> = {
  green_ratio: '녹지율',
  impervious_ratio: '인공표면 비율',
  road_ratio: '도로 비율',
  ndvi: '식생지수',
  albedo: '표면 반사율',
  building_ratio: '건물면적 비율'
};

const STANDARD_SCENARIO = '100m 격자 기준 표준 시나리오 · 격자 내 10% 수준 개입';

export const POLICY_PRESETS = [
  {
    id: 'paved_to_green',
    name: '포장공간 녹지화',
    description: '100m 격자 내 포장공간 10%p를 녹지로 전환',
    changes: {
      green_ratio: 0.1,
      impervious_ratio: -0.1,
      ndvi: 0.05
    },
    affectedFeatures: ['green_ratio', 'impervious_ratio', 'ndvi'],
    assumptions: [
      '영향 비율 0.10',
      '기존 포장면 NDVI 0.00, 전환 후 녹지 NDVI 0.50으로 가정'
    ],
    sourceUrl: 'https://news.seoul.go.kr/env/archives/561644',
    scenarioLabel: STANDARD_SCENARIO,
    minimumRequirements: [
      {
        feature: 'impervious_ratio',
        minimum: 0.1,
        unavailableMessage: '현재 격자에는 표준 정책 시나리오를 적용할 충분한 포장공간이 없습니다.'
      }
    ]
  },
  {
    id: 'road_to_green',
    name: '도로 가로녹지화',
    description: '100m 격자 내 도로공간 10%p를 녹지로 전환',
    changes: {
      road_ratio: -0.1,
      green_ratio: 0.1,
      impervious_ratio: -0.1,
      ndvi: 0.05
    },
    affectedFeatures: ['road_ratio', 'green_ratio', 'impervious_ratio', 'ndvi'],
    assumptions: [
      '영향 비율 0.10',
      '기존 도로 NDVI 0.00, 전환 후 가로녹지 NDVI 0.50으로 가정'
    ],
    sourceUrl: 'https://news.seoul.go.kr/traffic/?p=505617',
    scenarioLabel: STANDARD_SCENARIO,
    minimumRequirements: [
      {
        feature: 'road_ratio',
        minimum: 0.1,
        unavailableMessage: '현재 격자에는 표준 정책 시나리오를 적용할 충분한 도로공간이 없습니다.'
      },
      {
        feature: 'impervious_ratio',
        minimum: 0.1,
        unavailableMessage: '현재 격자에는 표준 정책 시나리오를 적용할 충분한 인공표면이 없습니다.'
      }
    ]
  },
  {
    id: 'vegetation_improvement',
    name: '기존 녹지 개선',
    description: '100m 격자 내 기존 녹지 일부의 식생 활력 및 수관 개선',
    changes: {
      ndvi: 0.02
    },
    affectedFeatures: ['ndvi'],
    assumptions: [
      '영향 비율 0.10',
      '기존 식생 NDVI 0.35, 개선 후 NDVI 0.55로 가정',
      '녹지 면적 자체는 늘어나지 않는 시나리오'
    ],
    sourceUrl: 'https://news.seoul.go.kr/env/archives/567149',
    scenarioLabel: STANDARD_SCENARIO,
    minimumRequirements: [
      {
        feature: 'green_ratio',
        minimum: 0.1,
        unavailableMessage: '현재 격자에는 표준 식생 개선 시나리오를 적용할 충분한 기존 녹지가 없습니다.'
      }
    ]
  },
  {
    id: 'small_park',
    name: '소규모 공원·정원 조성',
    description: '100m 격자 내 포장·유휴공간 10%p를 공원 또는 정원으로 전환',
    changes: {
      green_ratio: 0.1,
      impervious_ratio: -0.1,
      ndvi: 0.05
    },
    affectedFeatures: ['green_ratio', 'impervious_ratio', 'ndvi'],
    assumptions: [
      '영향 비율 0.10',
      '기존 포장면 NDVI 0.00, 전환 후 녹지 NDVI 0.50으로 가정',
      'v1에서는 공원 거리와 반경 500m 공원면적을 재계산하지 않음'
    ],
    sourceUrl: 'https://news.seoul.go.kr/env/archives/511578',
    scenarioLabel: STANDARD_SCENARIO,
    minimumRequirements: [
      {
        feature: 'impervious_ratio',
        minimum: 0.1,
        unavailableMessage: '현재 격자에는 표준 공원·정원 시나리오를 적용할 충분한 포장·유휴공간이 없습니다.'
      }
    ]
  },
  {
    id: 'green_roof',
    name: '옥상녹화',
    description: '100m 격자 내부 건물 옥상에 표준 수준의 녹화 적용',
    changes: {
      ndvi: 0.03
    },
    affectedFeatures: ['ndvi'],
    assumptions: [
      '영향 비율 0.10',
      '기존 비식생 옥상 NDVI 0.00, 녹화 후 NDVI 0.30으로 가정',
      '건물면적 비율은 실제 사용 가능한 옥상면적과 같지 않음'
    ],
    sourceUrl: 'https://news.seoul.go.kr/env/archives/564339',
    scenarioLabel: STANDARD_SCENARIO,
    minimumRequirements: [
      {
        feature: 'building_ratio',
        minimum: 0.1,
        unavailableMessage: '현재 격자의 건물면적 비율이 표준 옥상녹화 시나리오 기준보다 작습니다.'
      }
    ]
  },
  {
    id: 'cool_roof',
    name: '쿨루프',
    description: '100m 격자 내 지붕 10% 수준에 고반사 소재 적용',
    changes: {
      albedo: 0.04
    },
    affectedFeatures: ['albedo'],
    assumptions: [
      '영향 비율 0.10',
      '기존 지붕 albedo 0.20, 쿨루프 albedo 0.60으로 가정',
      '건물면적 비율은 실제 시공 가능한 지붕면적과 같지 않음'
    ],
    sourceUrl: 'https://news.seoul.go.kr/env/archives/43080',
    scenarioLabel: STANDARD_SCENARIO,
    minimumRequirements: [
      {
        feature: 'building_ratio',
        minimum: 0.1,
        unavailableMessage: '현재 격자의 건물면적 비율이 표준 쿨루프 시나리오 기준보다 작습니다.'
      }
    ]
  }
] as const satisfies readonly PolicyPreset[];

/** 프리셋 요청은 별도 옵션 필드나 자동 토지피복 연동에 의존하지 않는다. */
export function policySimulationRequest(
  gridId: string,
  preset: PolicyPreset
): SimulationRequest {
  const changes = Object.fromEntries(
    preset.affectedFeatures.map((feature) => {
      const delta = preset.changes[feature];
      if (typeof delta !== 'number') {
        throw new Error(`${preset.id} 정책의 ${feature} 변화량이 없습니다.`);
      }
      return [feature, delta];
    })
  ) as Record<string, number>;

  return {
    grid_id: gridId,
    changes,
    couple_land_cover: false
  };
}

function featureValue(properties: GridAnalysisProperties, feature: PolicyFeature): number | null {
  const value = properties[feature];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** 표준 변화량이 공간 조건과 모델 학습범위 안에서 온전히 적용되는지 확인한다. */
export function policyApplicability(
  preset: PolicyPreset,
  properties: GridAnalysisProperties,
  ranges: Record<string, FeatureRange> | null
): PolicyApplicability {
  for (const requirement of preset.minimumRequirements) {
    const current = featureValue(properties, requirement.feature);
    if (current == null) {
      return {
        applicable: false,
        reason: `${POLICY_FEATURE_LABELS[requirement.feature]} 데이터가 없어 적용 가능성을 확인할 수 없습니다.`
      };
    }
    if (current < requirement.minimum - 1e-9) {
      return { applicable: false, reason: requirement.unavailableMessage };
    }
  }

  if (!ranges) {
    return { applicable: false, reason: '모델 적용 범위를 확인하고 있습니다.' };
  }

  for (const feature of preset.affectedFeatures) {
    const current = featureValue(properties, feature);
    if (current == null) {
      return {
        applicable: false,
        reason: `${POLICY_FEATURE_LABELS[feature]} 데이터가 없어 이 정책을 적용할 수 없습니다.`
      };
    }

    const range = ranges[feature];
    if (!range) {
      return {
        applicable: false,
        reason: `${POLICY_FEATURE_LABELS[feature]}의 모델 적용 범위를 확인할 수 없습니다.`
      };
    }

    const delta = preset.changes[feature] ?? 0;
    const after = current + delta;
    if (after < range.min - 1e-9 || after > range.max + 1e-9) {
      const direction = delta >= 0 ? '늘리면' : '줄이면';
      return {
        applicable: false,
        reason: `현재 격자에서 ${POLICY_FEATURE_LABELS[feature]}을 표준 변화량만큼 ${direction} 모델 적용 범위를 벗어납니다.`
      };
    }
  }

  return { applicable: true };
}
