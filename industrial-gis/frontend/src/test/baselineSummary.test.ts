import { describe, expect, it } from 'vitest';
import { baselineSummary, deviationFor } from '@/components/baselineSummary';

const known = {
  baseline_scope: 'facility' as const, baseline_known: true, baseline_active_fraction: 0.713,
  baseline_covered_days: 365, baseline_frp_median: 3.24, baseline_frp_p95: 9.8, baseline_night_share: 0.91,
  baseline_monthly: [0.1, 0.2, 0.5, 0.9, 1, 0.8, 0.4, 0.3, 0.2, 0.1, 0, null], frp_today: 14.12, frp_z: 4.23,
};

describe('baselineSummary', () => {
  it('states how often the place is normally active and how today compares', () => {
    const s = baselineSummary(known);
    expect(s.available).toBe(true);
    expect(s.headline).toBe('Normally active 71% of days at this facility');
    expect(s.detail).toContain('over 365 covered days');
    expect(s.detail).toContain('typical 3.2 MW (95th percentile 9.8 MW)');
    expect(s.detail).toContain('91% of detections at night');
    expect(s.today).toBe('14.1 MW on its latest day (+4.2 z)');
    expect(s.deviation).toBe('far_above');
    expect(s.monthly).toHaveLength(12);
  });

  it('treats a place with no history as new heat, not as a routine source', () => {
    const s = baselineSummary({ ...known, baseline_scope: 'cell', baseline_known: false, baseline_active_fraction: 0,
      baseline_frp_median: null, baseline_frp_p95: null, baseline_night_share: null, baseline_monthly: null,
      frp_today: 30, frp_z: 30 });
    expect(s.deviation).toBe('new');
    expect(s.headline).toBe('Never seen at this 1 km cell in the last 365 days');
    expect(s.today).toContain('30.0 MW');
  });

  it('says so when no baseline was built', () => {
    const s = baselineSummary({ ...known, baseline_scope: null, baseline_active_fraction: null, baseline_covered_days: null });
    expect(s.available).toBe(false);
    expect(s.headline).toBe('No baseline built yet');
  });

  it('classifies the z-score', () => {
    expect([5, 2.5, 0.3, -3, null].map(deviationFor)).toEqual(['far_above', 'elevated', 'usual', 'below', 'unknown']);
  });
});
