export interface IndustrialSiteProperties {
  site_id: string;
  name: string;
  normalized_name: string;
  industry_type: string;
  normalized_industry_type: string;
  status: string;
  state: string;
  district: string;
  address: string;
  latitude: number | null;
  longitude: number | null;
  /** OSM element references such as "way/123456" (live data) */
  osm_ids: string[];
  government_ids: string[];
  source_ids: string[];
  data_sources: string[];
  source_count: number;
  match_score: number;
  /** Cross-source match confidence (legacy field; "unmatched" while only OSM is integrated) */
  match_confidence: string;
  match_method: string;
  review_required: boolean;
  last_verified: string;
  /** Calculated tag-completeness level. NOT an official verification. */
  evidence_level?: 'high' | 'medium' | 'low' | string;
  evidence_score?: number;
  feature_class?: string;
  geometry_type?: string;
  primary_source?: string;
  source_ref?: string;
  source_url?: string;
  source_updated_at?: string | null;
  osm_version?: number | null;
  revision?: number;
  updated_at?: string | null;
  /** Detail endpoint only */
  tags?: Record<string, string>;
  area_m2?: number | null;
}

export interface BoundaryProperties {
  boundary_level: 'india' | 'states' | 'districts';
  name: string;
  source_id: string;
  source: string;
}

export interface PointGeometry {
  type: 'Point';
  coordinates: [number, number];
}

export interface PolygonGeometry {
  type: 'Polygon' | 'MultiPolygon';
  coordinates: unknown;
}

export interface IndustrialSiteFeature {
  type: 'Feature';
  geometry: PointGeometry | PolygonGeometry;
  properties: IndustrialSiteProperties;
}

export interface BoundaryFeature {
  type: 'Feature';
  geometry: PolygonGeometry;
  properties: BoundaryProperties;
}

export interface CollectionMeta {
  total: number;
  returned: number;
  limit: number;
  offset: number;
  truncated: boolean;
  revision: number;
  synthetic?: boolean;
}

export interface FeatureCollection<TFeature> {
  type: 'FeatureCollection';
  features: TFeature[];
  meta?: CollectionMeta;
}

export interface StatisticsResponse {
  total_sites: number;
  by_state: Record<string, number>;
  by_district?: Record<string, number>;
  by_industry_type: Record<string, number>;
  by_status: Record<string, number>;
  by_feature_class?: Record<string, number>;
  osm_records: number;
  government_records: number;
  matched_records: number;
  unmatched_records: number;
  high_confidence_matches: number;
  confidence_distribution: Record<string, number>;
  distinct_states?: number;
  distinct_districts?: number;
  revision?: number;
  scope?: { description: string; filters: Record<string, string> };
}

export interface FilterOptionsResponse {
  states: string[];
  districts: string[];
  industry_types: string[];
  statuses: string[];
  confidence_levels: string[];
  feature_classes?: string[];
}

export interface IndustryQueryFilters {
  bbox?: string;
  state?: string;
  district?: string;
  industry_type?: string;
  status?: string;
  confidence?: string;
}

export interface HealthResponse {
  status: string;
  data_mode: 'postgis' | 'synthetic' | string;
  synthetic?: boolean;
  database?: { status: 'ok' | 'unavailable' | 'error' | 'not_used' };
  ready?: boolean;
  revision?: number;
  dataset?: { active_sites: number; boundary_states?: number; boundary_districts?: number; label?: string };
  event_stream?: { listening: boolean };
}

export type SyncState =
  | 'live' | 'syncing' | 'delayed' | 'offline' | 'not_configured' | 'awaiting_setup';

export interface SyncSource {
  source: string;
  kind: string | null;
  state: SyncState;
  reason: string;
  enabled: boolean;
  update_cadence: string | null;
  poll_interval_seconds: number | null;
  freshness_target_seconds: number | null;
  last_check_at: string | null;
  last_check_age_seconds: number | null;
  last_source_data_at: string | null;
  data_age_seconds: number | null;
  source_head_timestamp: string | null;
  source_head_published_at: string | null;
  lag_behind_source_seconds: number | null;
  replication_steps_behind: number | null;
  last_commit_at: string | null;
  last_success_at: string | null;
  consecutive_failures: number;
  next_retry_at: string | null;
  last_error: string | null;
  totals: Record<string, number | string>;
}

export interface SyncStatusResponse {
  data_mode: string;
  api: { alive: boolean; event_stream_listening?: boolean };
  database: { status: string };
  overall: { state: SyncState; reason: string; source?: string };
  sources: SyncSource[];
  revision: number | null;
  revision_changed_at?: string | null;
  time: string;
}

export interface RevisionEvent {
  revision: number;
  source?: string;
  created?: number;
  updated?: number;
  deleted?: number;
  committed_at?: string;
  source_data_at?: string | null;
}

// ── Thermal sources (NASA FIRMS, classified) ──────────────────────────────────

export type ThermalClassId =
  | 'industrial_persistent' | 'industrial_fire_candidate' | 'agricultural_burning'
  | 'vegetation_fire' | 'unmapped_persistent' | 'other';

export interface ThermalClassInfo {
  id: ThermalClassId;
  label: string;
  color: string;
  description: string;
}

export interface ThermalDetection {
  detection_id: string;
  time: string;
  instrument: string;
  frp: number;
  confidence: string;
  daynight: string;
  lat: number;
  lon: number;
}

export interface ThermalProperties {
  source_id: string;
  thermal_class: ThermalClassId;
  thermal_label: string;
  class_confidence: 'high' | 'medium' | 'low';
  class_margin: number;
  class_scores: Record<string, number>;
  reasons: string[];
  alert_level: 'watch' | 'high' | 'critical' | null;
  alert_text: string | null;
  state: string | null;
  site_ref: string | null;
  site_name: string | null;
  site_kind: string | null;
  site_distance_m: number | null;
  site_within: boolean;
  n_detections: number;
  n_days: number;
  window_days: number;
  first_seen: string;
  last_seen: string;
  frp_mean: number;
  frp_max: number;
  frp_total: number;
  night_fraction: number;
  extent_m: number;
  lc_dominant?: string | null;
  lc_crop?: number | null;
  lc_vegetation?: number | null;
  lc_built?: number | null;
  lc_bare?: number | null;
  instruments: string;
  /** 365-day history of the same facility / 1 km cell (null/absent when no baseline has been built). */
  baseline_scope?: 'facility' | 'cell' | null;
  baseline_key?: string | null;
  baseline_known?: boolean;
  baseline_active_fraction?: number | null;
  baseline_covered_days?: number | null;
  baseline_frp_median?: number | null;
  baseline_frp_p95?: number | null;
  baseline_night_share?: number | null;
  baseline_monthly?: (number | null)[] | null;
  baseline_window_end?: string | null;
  frp_today?: number | null;
  frp_today_date?: string | null;
  frp_z?: number | null;
  /** trained model's second opinion next to the rule class (absent when no model is deployed) */
  model_version?: string | null;
  model_class?: 'industrial_persistent' | 'agricultural_burning' | 'vegetation_fire' | null;
  model_probability?: number | null;
  model_probabilities?: Record<string, number> | null;
  model_reasons?: string[] | null;
  /** satellite evidence persisted by the worker (alerts / industrial candidates) */
  imagery_available?: boolean | null;
  imagery_scene_id?: string | null;
  imagery_scene_time?: string | null;
  imagery_cloud_percent?: number | null;
  imagery_dnbr_p95?: number | null;
  imagery_burned_fraction?: number | null;
  imagery_swir_hot_pixels?: number | null;
  imagery_landsat_excess_k?: number | null;
  detections?: ThermalDetection[];
}

export interface ThermalFeature {
  type: 'Feature';
  geometry: { type: 'Point'; coordinates: [number, number] };
  properties: ThermalProperties;
}

export interface ThermalStats {
  total_sources: number;
  by_class: Record<string, number>;
  by_state: Record<string, number>;
  alerts: { watch: number; high: number; critical: number };
  total_frp_mw: number;
  detections: number;
  backend: string;
}

export interface ThermalStatus {
  available: boolean;
  backend?: string;
  reason?: string;
  summary?: { sources?: number; detections?: number; window_days?: number; alerts?: number; facilities_indexed?: number };
  status?: { last_success_at?: string; latest_detection_at?: string; last_error?: string | null; state?: string };
  replay?: { active: boolean; finished?: boolean; scenario: string; title: string; virtual_time: string; from: string; to: string; speed?: number | null };
  freshness?: { state: 'live' | 'delayed' | 'replay'; last_refresh_age_seconds: number | null; latest_detection_age_seconds: number | null; note: string };
  attribution?: string[];
}

export interface ThermalFilters {
  thermal_class?: string;
  min_frp?: number;
  alerts_only?: boolean;
  hours?: number;
  state?: string;
}

export interface ThermalImageryScene {
  id: string;
  datetime: string;
  cloud_cover_percent: number;
  offset_days?: number | null;
  platform?: string | null;
  mgrs_tile?: string | null;
  chip_cloud_fraction?: number | null;
  gap_days?: number | null;
  same_tile?: boolean | null;
}

export interface ThermalImageryPair {
  true_colour: string;
  swir: string;
}

export interface ThermalImagery {
  available: boolean;
  reason: string | null;
  window_km: number;
  /** earlier single-scene shape (kept so a cached older response still renders) */
  true_colour?: string;
  swir?: string;
  scene?: ThermalImageryScene;
  before_scene?: ThermalImageryScene | null;
  images?: { after?: ThermalImageryPair; before?: ThermalImageryPair };
  indices?: {
    nbr_before: number; nbr_after: number; dnbr_mean: number; dnbr_p95: number; burned_fraction: number;
    valid_fraction: number; severity: string;
  } | null;
  hot_pixels?: { count: number; fraction: number; rule: string } | null;
  landsat?: {
    available: boolean; reason?: string | null; window_days?: number; source?: string;
    scene?: ThermalImageryScene; st_max_k?: number; st_p99_k?: number; st_median_k?: number; st_excess_k?: number;
  } | null;
  composites?: { true_colour: string; swir: string };
  attribution?: string;
}

export interface FacilityProperties {
  facility_id: string;
  name: string | null;
  kind: string;
  radius_m: number;
  capacity_mw: number | null;
  status: string | null;
  primary_source: string;
  source_count: number;
  sources: string;
  attribution: string;
  fuel?: string | null;
  has_footprint?: boolean;
}

export interface FacilityFeature {
  type: 'Feature';
  geometry: PointGeometry;
  properties: FacilityProperties;
}

export interface FacilitySourceInfo { id: string; name: string; licence: string; attribution: string }

export interface FacilityCollection {
  type: 'FeatureCollection';
  features: FacilityFeature[];
  meta: { available: boolean; total: number; returned: number; truncated?: boolean; sources: FacilitySourceInfo[]; reason?: string };
}

export interface FacilitySourceRecord {
  facility_id: string;
  source: string;
  source_id: string;
  name: string | null;
  distance_m: number | null;
  url: string | null;
  licence: string;
  attribution: string;
}

export interface FacilityDetail extends FacilityFeature {
  footprint: { type: string; coordinates: unknown } | null;
  source_records: FacilitySourceRecord[];
}

// ---- Phase 6: alerts and the analyst loop --------------------------------------------------------------------------
export type AlertSeverity = 'watch' | 'high' | 'critical';
export type AlertStatus = 'open' | 'confirmed' | 'rejected' | 'unsure';
export type AlertDecision = 'confirmed' | 'rejected' | 'unsure';

export interface AlertDelivery { ok: boolean; at: string | null; error: string | null; attempts?: number }
export interface AlertImagery {
  imagery_available: boolean; imagery_scene_id?: string | null; imagery_scene_time?: string | null; imagery_cloud_percent?: number | null;
  imagery_dnbr_p95?: number | null; imagery_swir_hot_pixels?: number | null; imagery_landsat_excess_k?: number | null;
}
export interface AlertTimelineEvent { at: string; kind: 'first_detection' | 'peak' | 'last_detection' | 'alert' | 'imagery' | 'delivery' | 'decision'; text: string }
export interface AlertDecisionRecord { id: number; decision: AlertDecision; note: string | null; analyst: string | null; decided_at: string; label_id: string | null }

export interface ThermalAlert {
  alert_id: number; facility_key: string; source_id: string; site_ref: string | null; site_name: string | null; site_kind: string | null;
  state: string | null; trigger: 'p_fire' | 'frp_z' | 'both' | 'test'; severity: AlertSeverity; p_fire: number | null; frp_max: number | null;
  frp_z: number | null; first_seen: string | null; last_seen: string | null; peak_frp: number | null; peak_at: string | null; summary: string;
  reasons: string[] | null; imagery: AlertImagery | null; status: AlertStatus; delivery: Record<string, AlertDelivery>; is_test: boolean;
  created_at: string; updated_at: string; lat: number; lon: number;
  timeline?: AlertTimelineEvent[]; decisions?: AlertDecisionRecord[];
}
export interface AlertList { available: boolean; alerts: ThermalAlert[]; channels: string[]; decisions_enabled: boolean; demo_readonly?: boolean }

// ---- Phase 8: dashboard ---------------------------------------------------------------------------------------------
export interface DashboardModelCard {
  model_version: string | null;
  macro_f1: { model: number | null; rules: number | null; blind: number | null };
  macro_f1_ci: { model: [number, number] | null; rules: [number, number] | null; blind: [number, number] | null };
  test_units: number | null;
  incidents: { verified: number | null; with_detection: number | null; 'model_recall_of_detected_at_0.25_false_alarms_per_day': number | null; rules_recall_of_detected: number | null };
  caveat: string;
}
export interface DashboardData {
  backend: 'postgis' | 'snapshot';
  generated_at: string;
  totals: { sources: number; detections: number; newest_detection: string | null };
  by_class: Record<string, number>;
  top_states: { state: string; sources: number; industrial: number; flagged: number }[];
  alerts: { today: number; today_by_severity: Record<string, number>; open: number; by_status: Record<string, number>;
            last_7_days: { date: string; count: number }[]; note?: string };
  class_mix: { at: string; by_class: Record<string, number> }[];
  model: DashboardModelCard | null;
}
