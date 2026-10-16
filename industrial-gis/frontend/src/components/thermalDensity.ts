import type { ThermalFeature } from '@/types/industrial';

/** Below this zoom the national picture is a heat-density grid plus the industrial sources as points. */
export const DENSITY_ZOOM = 8;

const INDUSTRIAL = new Set(['industrial_persistent', 'industrial_fire_candidate', 'unmapped_persistent']);

export interface DensityCell {
  key: string;
  south: number;
  west: number;
  size: number;
  count: number;
  maxFrp: number;
  totalFrp: number;
  byClass: Record<string, number>;
}

/** Grid size in degrees for a zoom level: about 1 degree for the whole country, 0.25 just before points take over. */
export function cellSize(zoom: number): number {
  return zoom <= 5 ? 1 : zoom === 6 ? 0.5 : 0.25;
}

/** A source that must stay a point at every zoom: industrial classes, anything that raised an alert, or the selected one. */
export function staysPoint(f: ThermalFeature, selectedId?: string | null): boolean {
  const p = f.properties;
  return INDUSTRIAL.has(p.thermal_class) || !!p.alert_level || p.source_id === selectedId;
}

/** Split the sources for the zoom: points to draw, and the heat-density cells for the rest. */
export function splitForZoom(features: ThermalFeature[], zoom: number, selectedId?: string | null): { points: ThermalFeature[]; cells: DensityCell[] } {
  if (zoom >= DENSITY_ZOOM) return { points: features, cells: [] };
  const size = cellSize(zoom);
  const points: ThermalFeature[] = [];
  const cells = new Map<string, DensityCell>();
  for (const f of features) {
    if (staysPoint(f, selectedId)) { points.push(f); continue; }
    const [lon, lat] = f.geometry.coordinates;
    const south = Math.floor(lat / size) * size;
    const west = Math.floor(lon / size) * size;
    const key = `${south.toFixed(3)}_${west.toFixed(3)}`;
    let c = cells.get(key);
    if (!c) { c = { key, south, west, size, count: 0, maxFrp: 0, totalFrp: 0, byClass: {} }; cells.set(key, c); }
    c.count += 1;
    c.maxFrp = Math.max(c.maxFrp, f.properties.frp_max);
    c.totalFrp += f.properties.frp_total ?? f.properties.frp_max;
    c.byClass[f.properties.thermal_class] = (c.byClass[f.properties.thermal_class] ?? 0) + 1;
  }
  return { points, cells: [...cells.values()] };
}

/** Fill opacity 0.15..0.75 from the cell's source count relative to the busiest cell (square-root scale). */
export function cellOpacity(count: number, maxCount: number): number {
  if (maxCount <= 0) return 0.15;
  return 0.15 + 0.6 * Math.sqrt(Math.min(count, maxCount) / maxCount);
}

export function dominantClass(c: DensityCell): string {
  return Object.entries(c.byClass).sort((a, b) => b[1] - a[1])[0]?.[0] ?? 'other';
}
