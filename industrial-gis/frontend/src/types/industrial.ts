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
  latitude: number;
  longitude: number;
  osm_ids: number[];
  government_ids: string[];
  source_ids: string[];
  data_sources: string[];
  source_count: number;
  match_score: number;
  match_confidence: string;
  match_method: string;
  review_required: boolean;
  last_verified: string;
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
  geometry: PointGeometry;
  properties: IndustrialSiteProperties;
}

export interface BoundaryFeature {
  type: 'Feature';
  geometry: PolygonGeometry;
  properties: BoundaryProperties;
}

export interface FeatureCollection<TFeature> {
  type: 'FeatureCollection';
  features: TFeature[];
}

export interface StatisticsResponse {
  total_sites: number;
  by_state: Record<string, number>;
  by_industry_type: Record<string, number>;
  by_status: Record<string, number>;
  osm_records: number;
  government_records: number;
  matched_records: number;
  unmatched_records: number;
  high_confidence_matches: number;
  confidence_distribution: Record<string, number>;
}

export interface FilterOptionsResponse {
  states: string[];
  districts: string[];
  industry_types: string[];
  statuses: string[];
  confidence_levels: string[];
}

export interface IndustryQueryFilters {
  bbox?: string;
  state?: string;
  district?: string;
  industry_type?: string;
  status?: string;
  confidence?: string;
}
