import type { FeatureCollection, ThermalFeature } from '@/types/industrial';

/** Pure helpers for the 3D globe: data shaping and NASA GIBS tile URLs (kept free of WebGL so they are unit-testable). */

export const INDUSTRIAL_CLASSES = ['industrial_persistent', 'industrial_fire_candidate', 'unmapped_persistent'];
const HALF_DEG = 0.011;   // ~1.2 km: about one VIIRS pixel (375 m) drawn a few times larger so it is visible from the air

/** Column height in metres for a source of the given peak power (MW). sqrt keeps a 2000 MW flare from dwarfing everything. */
export function columnHeight(frpMax: number, scale = 1): number {
  const f = Number.isFinite(frpMax) && frpMax > 0 ? frpMax : 0;
  return Math.round((800 + Math.sqrt(f) * 1800) * scale);
}

export interface GlobeStats { total: number; industrial: number; alerts: number; maxFrp: number }

export function globeStats(fc: FeatureCollection<ThermalFeature> | null): GlobeStats {
  const s: GlobeStats = { total: 0, industrial: 0, alerts: 0, maxFrp: 0 };
  for (const f of fc?.features ?? []) {
    s.total += 1;
    if (INDUSTRIAL_CLASSES.includes(f.properties.thermal_class)) s.industrial += 1;
    if (f.properties.alert_level) s.alerts += 1;
    s.maxFrp = Math.max(s.maxFrp, f.properties.frp_max ?? 0);
  }
  return s;
}

type Geo = { type: 'FeatureCollection'; features: unknown[] };

/** Points (glow + hit targets) and square footprints (3D columns) for every source. Column height is in the `h` property. */
export function toGlobeGeoJSON(fc: FeatureCollection<ThermalFeature> | null, colors: Record<string, string>, scale = 1): { points: Geo; columns: Geo } {
  const points: unknown[] = [];
  const columns: unknown[] = [];
  for (const f of fc?.features ?? []) {
    const g = f.geometry as { type: string; coordinates: [number, number] };
    if (!g || g.type !== 'Point') continue;
    const [lon, lat] = g.coordinates;
    const p = f.properties;
    const props = {
      id: p.source_id, cls: p.thermal_class, label: p.thermal_label, color: colors[p.thermal_class] ?? '#ea580c',
      frp: Math.round((p.frp_max ?? 0) * 10) / 10, alert: p.alert_level ?? '', site: p.site_name ?? '',
      ind: INDUSTRIAL_CLASSES.includes(p.thermal_class) ? 1 : 0, h: columnHeight(p.frp_max ?? 0, scale),
    };
    points.push({ type: 'Feature', properties: props, geometry: { type: 'Point', coordinates: [lon, lat] } });
    const dx = HALF_DEG / Math.max(Math.cos((lat * Math.PI) / 180), 0.2);
    columns.push({
      type: 'Feature', properties: props,
      geometry: { type: 'Polygon', coordinates: [[[lon - dx, lat - HALF_DEG], [lon + dx, lat - HALF_DEG], [lon + dx, lat + HALF_DEG], [lon - dx, lat + HALF_DEG], [lon - dx, lat - HALF_DEG]]] },
    });
  }
  return { points: { type: 'FeatureCollection', features: points }, columns: { type: 'FeatureCollection', features: columns } };
}

export type GlobeBase = 'bluemarble' | 'truecolor' | 'night' | 'streets';

export const BASES: { id: GlobeBase; label: string; note: string }[] = [
  { id: 'bluemarble', label: 'Blue Marble', note: 'NASA Blue Marble shaded relief' },
  { id: 'truecolor', label: 'Daily satellite', note: 'NASA VIIRS true colour for the chosen day' },
  { id: 'night', label: 'Night lights', note: 'NASA Black Marble: lit cities and industry' },
  { id: 'streets', label: 'Streets', note: 'OpenStreetMap' },
];

const GIBS = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best';

/** yyyy-mm-dd of `daysAgo` days before `now` (UTC). */
export function isoDay(daysAgo: number, now = new Date()): string {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - daysAgo));
  return d.toISOString().slice(0, 10);
}

export function gibsTrueColor(date: string): string {
  return `${GIBS}/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/${date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`;
}
export function gibsFires(date: string): string {
  return `${GIBS}/VIIRS_SNPP_Thermal_Anomalies_375m_All/default/${date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.png`;
}
export const GIBS_BLUE_MARBLE = `${GIBS}/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg`;
export const GIBS_NIGHT = `${GIBS}/VIIRS_Black_Marble/default/2016-01-01/GoogleMapsCompatible_Level8/{z}/{y}/{x}.png`;
export const TERRAIN_TILES = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';

export const GLOBE_VIEWS: Record<string, { center: [number, number]; zoom: number; pitch: number; bearing: number; label: string }> = {
  india: { center: [80, 22], zoom: 4.2, pitch: 45, bearing: 0, label: 'India' },
  world: { center: [60, 20], zoom: 1.4, pitch: 0, bearing: 0, label: 'World' },
  west: { center: [72.6, 22.9], zoom: 8.5, pitch: 65, bearing: 20, label: 'Gujarat' },
  east: { center: [86, 24], zoom: 7.5, pitch: 65, bearing: -15, label: 'Jharkhand coal belt' },
};
