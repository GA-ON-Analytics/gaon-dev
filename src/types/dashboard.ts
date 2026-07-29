export type LayerKey =
  | 'priority_score'
  | 'mean_actual_anomaly'
  | 'mean_actual_lst'
  | 'green_delta_c'
  | 'green_ratio'
  | 'ndvi'
  | 'building_ratio'
  | 'impervious_ratio'
  | 'nearest_shelter_distance_m';

export type GridResolution = '100m' | '250m' | '500m';

export type PolicyOptionKey =
  | 'green_ratio_increase'
  | 'impervious_ratio_reduction'
  | 'park_area_expansion';

export interface SimulationScenarioParameters {
  green_ratio_delta: number;
  impervious_ratio_delta: number;
  park_area_m2: number;
}

export interface SimulationRequest {
  grid_id: string;
  policy_options?: PolicyOptionKey[];
  parameters?: SimulationScenarioParameters;
  /**
   * 임의 변수를 직접 바꾸는 자유형 입력. 값은 변화량(델타)이며 양수=증가, 음수=감소.
   * 예: { ndvi: 0.1, building_ratio: -0.2, albedo: 0.05 }
   *
   * policy_options/parameters는 히트맵의 3개 프리셋(녹지·불투수·공원)이고, 이쪽은 19개 변수를
   * 모두 다룰 수 있다. 백엔드(main.py의 _changes_from_payload)가 둘을 합쳐 처리하므로 함께 써도
   * 되고, 자유형만 써도 된다. 바꿀 수 있는 변수 목록은 GET /api/features 참고.
   */
  changes?: Record<string, number>;
}

export interface SimulationChangedFeature {
  before: number;
  after: number;
}

export interface SimulationResponse {
  grid_id: string;
  gu_name?: string;
  before_anomaly: number;
  after_anomaly: number;
  delta_c: number;
  uncertainty_std?: number;
  delta_std?: number;
  changed_features: Record<string, SimulationChangedFeature>;
  message: string;
  warnings?: string[];
  [key: string]: unknown;
}

// POST /api/simulate/batch 응답 (여러 격자에 같은 정책 → 평균 저감)
export interface BatchSimulationResponse {
  count: number;
  mean_delta_c: number | null;
  results: SimulationResponse[];
}

export interface GridAnalysisProperties {
  grid_id?: string;
  display_grid_id?: string;
  gu_code?: string | number;
  gu_name?: string;
  /**
   * 행정동 이름 (예: '이촌1동').
   * 약 1.4%는 null이다. 격자 중심이 동 폴리곤 밖(한강 수면 등)에 떨어져 조인이 구 경계를
   * 넘어간 경우로, 틀린 동명을 쓰느니 비워뒀다. null이면 구 이름만 표시할 것.
   * 250m/500m는 여러 동에 걸칠 수 있어 '면적이 가장 넓은 동'이 대표로 들어간다.
   */
  dong_name?: string | null;
  grid_size_m?: number;
  area_m2?: number;
  source_cell_count?: number;
  // 250/500m 집계 격자를 구성하는 100m 격자 grid_id 목록 (배치 시뮬레이션 대상)
  member_grid_ids?: string[];
  priority_score?: number;
  priority_rank?: number;
  mean_actual_anomaly?: number;
  seoul_anomaly?: number;
  pred_anomaly?: number;
  pred_anomaly_std?: number;
  green_delta_c?: number;
  observations?: number;
  est_population?: number;
  est_elderly?: number;
  est_population_density?: number;
  dong_elderly_ratio?: number;
  dong_avg_age?: number;
  dong_pop_density?: number;
  nearest_shelter_distance_m?: number;
  nearest_shelter_name?: string;
  nearest_shelter_addr?: string;
  nearest_shelter_lon?: number;
  nearest_shelter_lat?: number;
  shelter_count_within_500m?: number;
  shelter_capacity_within_500m?: number;
  building_form_group?: string;
  mean_actual_lst?: number;
  building_ratio?: number;
  // building_ratio가 위성 지표면 기반 추정값이면 true (VWorld 건물 도형 누락 보정)
  building_ratio_estimated?: boolean;
  green_ratio?: number;
  ndvi?: number;
  impervious_ratio?: number;
  avg_ground_floor_count?: number;
  max_ground_floor_count?: number;
  floor_area_ratio_proxy?: number;
  road_ratio?: number;
  zoning_residential_ratio?: number;
  zoning_commercial_ratio?: number;
  zoning_industrial_ratio?: number;
  zoning_green_ratio?: number;
  elevation_m?: number;
  slope_deg?: number;
  albedo?: number;
  nearest_park_distance_m?: number;
  park_area_within_500m?: number;
  nearest_stream_distance_m?: number;
  top1_feature?: string;
  top1_shap?: number;
  top2_feature?: string;
  top2_shap?: number;
  top3_feature?: string;
  top3_shap?: number;
  [key: string]: unknown;
}
