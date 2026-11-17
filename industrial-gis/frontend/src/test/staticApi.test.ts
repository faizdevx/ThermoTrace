import { describe, expect, it } from 'vitest';
import { filterSources } from '@/services/staticApi';
import type { FeatureCollection, ThermalFeature } from '@/types/industrial';

const f = (id: string, cls: string, frp: number, lastSeen: string, alert: string | null = null): ThermalFeature => ({
  type: 'Feature', geometry: { type: 'Point', coordinates: [80, 20] },
  properties: { source_id: id, thermal_class: cls, frp_max: frp, last_seen: lastSeen, alert_level: alert } as never,
});
const fc = {
  type: 'FeatureCollection',
  features: [
    f('a', 'industrial_persistent', 50, '2026-11-17T12:00:00Z', 'high'),
    f('b', 'agricultural_burning', 5, '2026-11-17T10:00:00Z'),
    f('c', 'industrial_fire_candidate', 20, '2026-11-14T12:00:00Z'),
  ],
} as FeatureCollection<ThermalFeature>;
const ids = (qs: string) => filterSources(fc, new URLSearchParams(qs)).features.map(x => x.properties.source_id);

describe('static thermal filters (same rules as the API)', () => {
  it('passes everything without filters', () => expect(ids('')).toEqual(['a', 'b', 'c']));
  it('filters by class list, including the none-selected marker', () => {
    expect(ids('thermal_class=industrial_persistent,industrial_fire_candidate')).toEqual(['a', 'c']);
    expect(ids('thermal_class=none-selected')).toEqual([]);
  });
  it('filters by minimum power and alerts only', () => {
    expect(ids('min_frp=10')).toEqual(['a', 'c']);
    expect(ids('alerts_only=true')).toEqual(['a']);
  });
  it('measures the hours window back from the newest source', () => {
    expect(ids('hours=24')).toEqual(['a', 'b']);
    expect(ids('hours=72')).toEqual(['a', 'b', 'c']);
  });
});
