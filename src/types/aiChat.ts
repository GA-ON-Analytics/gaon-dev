export type AiChatToolName = 'get_grid_data' | 'run_simulation';

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
}

export interface AiFeatureCatalogResponse {
  count: number;
  features: AiFeatureCatalogItem[];
}

export interface AiChatChangedFeature {
  before: number;
  after: number;
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
