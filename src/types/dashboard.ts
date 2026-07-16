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
  policy_options: PolicyOptionKey[];
  parameters: SimulationScenarioParameters;
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
  changed_features: Record<string, SimulationChangedFeature>;
  message: string;
  warnings?: string[];
  [key: string]: unknown;
}

export interface GridAnalysisProperties {
  grid_id?: string;
  display_grid_id?: string;
  gu_code?: string | number;
  gu_name?: string;
  grid_size_m?: number;
  area_m2?: number;
  source_cell_count?: number;
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
  shelter_count_within_500m?: number;
  shelter_capacity_within_500m?: number;
  building_form_group?: string;
  mean_actual_lst?: number;
  building_ratio?: number;
  green_ratio?: number;
  ndvi?: number;
  built_surface_ratio?: number;
  avg_ground_floor_count?: number;
  elevation_m?: number;
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
