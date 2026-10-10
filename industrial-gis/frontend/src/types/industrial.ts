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
  freshness?: { state: 'live' | 'delayed'; last_refresh_age_seconds: number | null; latest_detection_age_seconds: number | null; note: string };
  attribution?: string[];
}

export interface ThermalFilters {
  thermal_class?: string;
  min_frp?: number;
  alerts_only?: boolean;
  hours?: number;
  state?: string;
}

export interface ThermalImagery {
  available: boolean;
  reason: string | null;
  window_km: number;
  true_colour?: string;
  swir?: string;
  scene?: {
    id: string;
    datetime: string;
    cloud_cover_percent: number;
    offset_days: number;
    platform?: string | null;
    mgrs_tile?: string | null;
  };
  composites?: { true_colour: string; swir: string };
  attribution?: string;
}
