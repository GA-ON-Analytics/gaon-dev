export type AiChatToolName =
  | 'get_grid_data'
  | 'run_simulation'
  | 'simulate_policy'
  | 'get_field_source';

export interface AiChatRequest {
  message: string;
  selected_grid_id: string | null;
}

export interface AiFeatureCatalogItem {
  name: string;
  label: string;
  description: string;
  semantic_definition: string;
  unit: string;
  category: string;
  /**
   * 모델 학습범위. 이 밖의 값은 predict_core가 경계로 clip하므로 요청량 != 적용량이 된다.
   * predict_core.feature_meta()가 채운다 — 모델에 안 쓰이는 항목엔 없을 수 있다.
   */
  min?: number;
  max?: number;
  /**
   * 표시 규칙. 백엔드 `format_grid_field_value`와 같은 규칙을 카드에서 쓰기 위한 것이다.
   * 비율 필드는 원본이 0~1이라 그대로 보여주면 말풍선(16.45%)과 값이 달라 보인다.
   */
  is_ratio?: boolean;
  display_decimals?: number;
}

export interface AiFeatureCatalogResponse {
  count: number;
  features: AiFeatureCatalogItem[];
}

export interface AiChatChangedFeature {
  before: number;
  after: number;
}

/** `get_field_source`가 지표별로 돌려주는 출처. */
export interface AiChatFieldSource {
  label: string;
  source: string;
}

export interface AiChatToolData {
  success?: boolean;
  grid_id?: string;
  gu_name?: string;
  requested_fields?: string[];
  values?: Record<string, number>;
  requested_changes?: Record<string, number>;
  applied_changes?: Record<string, AiChatChangedFeature>;
  before_anomaly?: number;
  after_anomaly?: number;
  delta_c?: number;
  uncertainty_std?: number;
  delta_std?: number;
  interpretation_basis?: string;
  warnings?: string[];
  limitations?: string[];
  policy_direction_notes?: string[];
  /** `simulate_policy`가 채운다. 어떤 정책을 적용했는지. */
  policy_id?: string;
  policy_name?: string;
  policy_scenario_label?: string;
  policy_source_url?: string;
  /** `get_field_source`가 채운다. 필드명 → 라벨·출처. */
  sources?: Record<string, AiChatFieldSource>;
}

export interface AiChatResponse {
  answer: string;
  used_tools: AiChatToolName[];
  tool_data: AiChatToolData | null;
  warnings: string[];
  limitations: string[];
}

export interface AiChatMessage {
  role: 'user' | 'assistant';
  text: string;
  used_tools?: AiChatToolName[];
  tool_data?: AiChatToolData;
  warnings?: string[];
  limitations?: string[];
}
