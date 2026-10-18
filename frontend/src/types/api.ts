/**
 * API types.
 *
 * These mirror the backend contract exported to `contract.json` by
 * `python manage.py export_contract`. `contract.test.ts` asserts the example
 * payload in that file still has the shape assumed here, so a backend rename
 * fails CI instead of quietly producing `undefined` in the UI.
 *
 * Nothing in this file encodes a risk threshold. Levels are read as ordered
 * integers from `/api/v1/config/`; the rules themselves live only on the
 * server (D12).
 */

/** Severity ordinals. Order matters: `max()` is "which is worse". */
export const enum RiskLevel {
  LOW = 1,
  MODERATE = 2,
  HIGH = 3,
  SEVERE = 4,
  NO_TRAVEL = 5,
}

export interface LevelMeta {
  value: RiskLevel;
  name: string;
  label: string;
  hex: string;
  symbol: string;
  description: string;
}

export interface ThresholdBucket {
  label: string;
  level: string;
  range: string;
}

export interface ThresholdTable {
  variable: 'wind' | 'rain' | 'snow';
  unit: string;
  buckets: ThresholdBucket[];
}

export interface LoadPolicy {
  no_travel_wind_mph: number;
  heavy_load_wind_mph: number;
  heavy_load_threshold_lb: number;
  extra_heavy_wind_mph: number;
  extra_heavy_threshold_lb: number;
  boundary_inclusive: boolean;
  max_load_lb: number;
}

export interface VariableMapping {
  variable: string;
  fields: string[];
  measure: string;
}

export interface RankingConfig {
  criteria: string[];
  safety_gate_enabled: boolean;
  avg_risk_precision: number;
}

export interface SamplingConfig {
  spacings_mi: number[];
  default_spacing_mi: number;
}

export interface SliderConfig {
  max_offset_hours: number;
  semantics: 'departure_offset';
}

export interface Assumptions {
  truck_speed_factor: number;
  truck_min_avg_mph: number;
  truck_max_avg_mph: number;
  checkpoint_time_snap: string;
  weather_resolution: string;
  note: string;
}

/** `GET /api/v1/config/` -- the single source of truth for the rules. */
export interface AppConfig {
  levels: LevelMeta[];
  thresholds: ThresholdTable[];
  load_policy: LoadPolicy;
  variable_mapping: VariableMapping[];
  ranking: RankingConfig;
  sampling: SamplingConfig;
  slider: SliderConfig;
  assumptions: Assumptions;
}

/** Every API error code with its HTTP status, from the generated contract. */
export interface ErrorCodeMap {
  [code: string]: number;
}

// ---------------------------------------------------------------------------
// Analysis payloads
// ---------------------------------------------------------------------------

export interface GeocodePlace {
  lat: number;
  lon: number;
  display_name: string;
  country_code: string | null;
  admin1: string | null;
  source: string;
}

export interface VariableReading {
  level: string;
  value: number | null;
  unit: string;
  bucket: string;
  data_quality: string;
}

export interface CheckpointRiskDetail {
  level: string;
  level_value: RiskLevel;
  dominant_factor: string;
  data_quality: string;
  reasons: string[];
  wind: VariableReading;
  rain: VariableReading;
  snow: VariableReading;
}

export interface Checkpoint {
  lat: number;
  lon: number;
  distance_mi: number;
  is_origin: boolean;
  is_destination: boolean;
  /** ISO-8601 arrival time at the base departure. */
  eta: string;
  /** Index into `hourly_risk` of the hour containing `eta`. */
  eta_hour_index: number;
  /**
   * One risk ordinal per hour across the whole forecast window.
   *
   * This is what makes the slider free: the level at departure offset `h` is
   * `hourly_risk[eta_hour_index + h]`, an array lookup with no network call and
   * no rule logic in the browser (D7/D13).
   */
  hourly_risk: RiskLevel[];
  risk: CheckpointRiskDetail;
}

export interface MilesByLevel {
  LOW?: number;
  MODERATE?: number;
  HIGH?: number;
  SEVERE?: number;
  NO_TRAVEL?: number;
}

export interface RouteSummary {
  total_miles: number;
  miles_by_level: MilesByLevel;
  no_travel_miles: number;
  severe_miles: number;
  high_miles: number;
  moderate_miles: number;
  low_miles: number;
  avg_risk: number;
  worst_level: string;
  adverse_miles: number;
}

export interface RouteOption {
  index: number;
  id: string;
  label: string;
  source: string;
  /** GeoJSON `[lon, lat]` polyline. */
  geometry: [number, number][];
  road_miles: number;
  duration_s: number;
  truck_avg_mph: number;
  checkpoint_count: number;
  /** Absent on early stream `route` events; attached when `summary` arrives. */
  summary?: RouteSummary;
}

export interface CheckpointBundle {
  route_id: string;
  points: Checkpoint[];
}

/**
 * A summary tagged with the route it belongs to.
 *
 * The JSON payload sends summaries as a bare array in route order, but the
 * streaming `summary` event carries `route_id` and the store keys on it -- so
 * the store's copy needs the id on the type.
 */
export type RouteSummaryBundle = RouteSummary & { route_id: string };

export interface Recommendation {
  route_id: string | null;
  label: string | null;
  ranking: string[];
  rationale: string[];
  criterion_tally: Record<string, Record<string, number>>;
  all_blocked: boolean;
  safety_gate_enabled: boolean;
}

export interface AnalysisMeta {
  origin: GeocodePlace;
  destination: GeocodePlace;
  departure: string;
  load_lb: number;
  spacing_mi: number;
  slider_hours: number;
  hour_window_start: string;
  hour_window_end: string;
  hour_window_hours: number;
  time_snap: string;
  weather_locations: number;
  elapsed_ms: number;
  cached: boolean;
  truck: {
    speed_factor: number;
    min_avg_mph: number;
    max_avg_mph: number;
  };
}

export interface AnalysisPayload {
  meta: AnalysisMeta;
  routes: RouteOption[];
  summaries: RouteSummaryBundle[];
  checkpoints: CheckpointBundle[];
  recommendation: Recommendation;
  warnings: string[];
}

/** The live shape assembled in the store as the stream arrives. */
export interface Analysis extends AnalysisPayload {
  /** Progress bookkeeping for the loading UI. */
  progress: {
    routesReady: boolean;
    checkpointsLoaded: number;
    checkpointTotal: number;
  };
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

export interface TripRequest {
  origin: string;
  destination: string;
  departure: string;
  load_lb: number;
  spacing_mi?: number;
  slider_hours?: number;
  truck_speed_factor?: number;
  truck_min_avg_mph?: number;
  truck_max_avg_mph?: number;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}
