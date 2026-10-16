import { describe, expect, it } from 'vitest';
import { alertRow, decisionHint, openCount } from '@/components/alertView';
import type { ThermalAlert } from '@/types/industrial';

const base: ThermalAlert = {
  alert_id: 1, facility_key: 'osm:1', source_id: 's1', site_ref: 'osm:1', site_name: 'Jamnagar refinery', site_kind: 'refinery', state: 'Gujarat',
  trigger: 'both', severity: 'critical', p_fire: 0.82, frp_max: 120, frp_z: 4.2, first_seen: null, last_seen: null, peak_frp: 131.4, peak_at: null,
  summary: 's', reasons: null, imagery: null, status: 'open', delivery: {}, is_test: false, created_at: '2026-10-16T00:00:00Z',
  updated_at: '2026-10-16T00:00:00Z', lat: 22.3, lon: 69.8,
};

describe('alert inbox view-model', () => {
  it('summarises place, power, probability and z-score', () => {
    const r = alertRow(base);
    expect(r.title).toBe('Jamnagar refinery');
    expect(r.meta).toBe('refinery · Gujarat · 131 MW · P(fire) 82% · +4.2 sd vs own baseline');
    expect(r.isOpen).toBe(true);
    expect(r.channels).toMatch(/not delivered/);
  });

  it('hides the numbers a trigger did not use and marks tests', () => {
    const r = alertRow({ ...base, trigger: 'frp_z', is_test: true, site_name: null, site_ref: null, site_kind: null });
    expect(r.title).toBe('[TEST] Unmapped location');
    expect(r.meta).not.toMatch(/P\(fire\)/);
    expect(r.meta).toMatch(/sd vs own baseline/);
  });

  it('reports delivery per channel, failures in capitals', () => {
    const r = alertRow({ ...base, delivery: { telegram: { ok: true, at: 'x', error: null }, email: { ok: false, at: 'x', error: 'boom' } } });
    expect(r.channels).toBe('telegram sent, email FAILED');
  });

  it('counts only real open alerts', () => {
    expect(openCount([base, { ...base, status: 'confirmed' }, { ...base, is_test: true }])).toBe(1);
  });

  it('explains why decisions are unavailable', () => {
    expect(decisionHint(false, 'x')).toMatch(/ANALYST_TOKEN/);
    expect(decisionHint(true, '  ')).toMatch(/token/);
    expect(decisionHint(true, 'abc')).toBeNull();
  });
});
