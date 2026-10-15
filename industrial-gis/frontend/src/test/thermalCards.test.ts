import { describe, expect, it } from 'vitest';
import { thermalCardValues } from '@/components/ThermalCards';

const feat = (p: Record<string, unknown>) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: p });

describe('thermalCardValues', () => {
  it('counts thermal classes, alerts, new heat and facilities', () => {
    const v = thermalCardValues(
      { total_sources: 4, by_class: { industrial_persistent: 2, industrial_fire_candidate: 1 }, by_state: {},
        alerts: { watch: 1, high: 1, critical: 0 }, total_frp_mw: 0, detections: 9, backend: 'postgis' },
      { type: 'FeatureCollection', features: [
        feat({ baseline_scope: 'facility', baseline_known: true }), feat({ baseline_scope: 'cell', baseline_known: false }),
        feat({ baseline_scope: 'cell', baseline_known: false }), feat({ baseline_scope: 'facility', baseline_known: true }),
      ] } as never,
      { type: 'FeatureCollection', meta: { available: true, total: 3, returned: 3, sources: [] },
        features: [feat({ source_count: 2 }), feat({ source_count: 1 }), feat({ source_count: 3 })] } as never,
    );
    expect(v).toEqual({ sources: 4, alerts: 2, persistent: 2, fireCandidates: 1, newHeat: 2, facilities: 3, multiSource: 2 });
  });

  it('shows "-" (null) rather than zeros when nothing is loaded', () => {
    expect(thermalCardValues(null, null, null)).toEqual({ sources: null, alerts: null, persistent: null,
      fireCandidates: null, newHeat: null, facilities: null, multiSource: null });
  });
});
