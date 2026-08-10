import { useEffect, useState } from 'react';
import { getPolicyPresets } from '../services/api';
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

// 정책 정의는 백엔드가 원본이다(`backend/policy_presets.py`).
// 챗봇이 백엔드에서 돌기 때문에, 정의가 여기 있으면 챗봇이 정책 이름을 읽지
// 못한다. 복제하면 두 벌이 갈라지는 게 시간문제라(predict_core.py가 그랬다)
// 정의는 한 곳에 두고 화면은 `/api/policies`로 받아 쓴다.
let policyPresetsCache: readonly PolicyPreset[] | null = null;
let policyPresetsPromise: Promise<readonly PolicyPreset[]> | null = null;

export function loadPolicyPresets(): Promise<readonly PolicyPreset[]> {
  if (policyPresetsCache) return Promise.resolve(policyPresetsCache);
  if (!policyPresetsPromise) {
    policyPresetsPromise = getPolicyPresets()
      .then((res) => {
        policyPresetsCache = res.policies;
        return policyPresetsCache;
      })
      .catch(() => {
        policyPresetsPromise = null;   // 다음 격자 선택 때 다시 시도한다
        return [];
      });
  }
  return policyPresetsPromise;
}

export function usePolicyPresets(): readonly PolicyPreset[] | null {
  const [presets, setPresets] = useState<readonly PolicyPreset[] | null>(policyPresetsCache);
  useEffect(() => {
    if (policyPresetsCache) return;
    let alive = true;
    void loadPolicyPresets().then((loaded) => {
      if (alive) setPresets(loaded);
    });
    return () => {
      alive = false;
    };
  }, []);
  return presets;
}

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
