import { describe, expect, it } from 'vitest';
import { columnHeight, globeStats, gibsFires, gibsTrueColor, isoDay, toGlobeGeoJSON } from '@/components/globeData';
import type { FeatureCollection, ThermalFeature } from '@/types/industrial';

const feat = (id: string, cls: string, frp: number, alert: string | null = null, lon = 85, lat = 23): ThermalFeature => ({
  type: 'Feature', geometry: { type: 'Point', coordinates: [lon, lat] },
  properties: { source_id: id, thermal_class: cls, thermal_label: cls, frp_max: frp, alert_level: alert, site_name: null } as never,
});
const fc = (f: ThermalFeature[]) => ({ type: 'FeatureCollection', features: f }) as FeatureCollection<ThermalFeature>;

describe('globe data', () => {
  it('column height grows with power but sub-linearly, and survives bad input', () => {
    expect(columnHeight(0)).toBe(800);
    expect(columnHeight(100)).toBeGreaterThan(columnHeight(25));
    expect(columnHeight(2000)).toBeLessThan(columnHeight(100) * 6);
    expect(columnHeight(NaN)).toBe(800);
    expect(columnHeight(100, 2)).toBe(columnHeight(100) * 2);
  });
  it('counts industrial sources, alerts and the peak power', () => {
    const s = globeStats(fc([feat('a', 'industrial_persistent', 30, 'high'), feat('b', 'agricultural_burning', 80), feat('c', 'industrial_fire_candidate', 10)]));
    expect(s).toEqual({ total: 3, industrial: 2, alerts: 1, maxFrp: 80 });
    expect(globeStats(null).total).toBe(0);
  });
  it('builds one point and one closed square column per source', () => {
    const g = toGlobeGeoJSON(fc([feat('a', 'industrial_persistent', 30)]), { industrial_persistent: '#b91c1c' });
    expect(g.points.features).toHaveLength(1);
    const col = g.columns.features[0] as { geometry: { coordinates: number[][][] }; properties: { color: string; ind: number } };
    expect(col.geometry.coordinates[0]).toHaveLength(5);
    expect(col.geometry.coordinates[0][0]).toEqual(col.geometry.coordinates[0][4]);
    expect(col.properties.color).toBe('#b91c1c');
    expect(col.properties.ind).toBe(1);
  });
  it('formats GIBS urls and dates', () => {
    expect(isoDay(1, new Date('2026-10-17T05:00:00Z'))).toBe('2026-10-16');
    expect(gibsTrueColor('2026-10-16')).toContain('/2026-10-16/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg');
    expect(gibsFires('2026-10-16')).toContain('VIIRS_SNPP_Thermal_Anomalies_375m_All');
  });
});
