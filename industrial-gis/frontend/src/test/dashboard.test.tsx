import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Dashboard } from '@/components/Dashboard';
import { modelStory, pct, severityLine, stackedMix } from '@/components/dashboardView';
import type { DashboardData } from '@/types/industrial';

const data: DashboardData = {
  backend: 'postgis', generated_at: '2026-10-16T10:00:00Z',
  totals: { sources: 3000, detections: 9000, newest_detection: '2026-10-16T08:30:00+00:00' },
  by_class: { agricultural_burning: 2100, industrial_persistent: 130, industrial_fire_candidate: 170, vegetation_fire: 400, other: 200 },
  top_states: [{ state: 'Punjab', sources: 900, industrial: 12, flagged: 0 }, { state: 'Odisha', sources: 300, industrial: 120, flagged: 2 }],
  alerts: { today: 3, today_by_severity: { high: 1, watch: 2 }, open: 2, by_status: { open: 2, confirmed: 1 }, last_7_days: [] },
  class_mix: [
    { at: '2026-10-14T00:00:00Z', by_class: { agricultural_burning: 60, industrial_persistent: 40 } },
    { at: '2026-10-15T00:00:00Z', by_class: { agricultural_burning: 50, industrial_persistent: 25, vegetation_fire: 25 } },
  ],
  model: { model_version: 'thermal-gbm-x', macro_f1: { model: 0.993, rules: 0.809, blind: 0.633 },
    macro_f1_ci: { model: [0.987, 0.998], rules: [0.763, 0.848], blind: [0.589, 0.668] }, test_units: 1021,
    incidents: { verified: 34, with_detection: 20, 'model_recall_of_detected_at_0.25_false_alarms_per_day': 1, rules_recall_of_detected: 0.8 },
    caveat: 'Labels are rule-derived.' },
};

vi.mock('@/services/api', () => ({ fetchDashboard: () => Promise.resolve(data) }));
afterEach(cleanup);

describe('dashboard view-model', () => {
  it('stacks class shares so every time step ends at 100 %', () => {
    const s = stackedMix(data.class_mix)!;
    expect(s.classes).toEqual(['industrial_persistent', 'agricultural_burning', 'vegetation_fire']);
    for (let i = 0; i < 2; i++) expect(s.tops[s.classes.length - 1][i]).toBeCloseTo(1, 5);
    expect(s.tops[0][0]).toBeCloseTo(0.4, 5);
    expect(s.totals).toEqual([100, 100]);
  });

  it('needs two points to draw a series', () => {
    expect(stackedMix(data.class_mix.slice(0, 1))).toBeNull();
    expect(stackedMix([])).toBeNull();
  });

  it('writes the model story with its caveat and the incident recall', () => {
    const t = modelStory(data.model!);
    expect(t).toMatch(/macro-F1 0.99 against 0.81/);
    expect(t).toMatch(/VIIRS saw 20/);
    expect(t).toMatch(/flags 100% of those/);
    expect(t).toMatch(/rule-derived/);
    expect(pct(null)).toBe('n/a');
    expect(severityLine({ watch: 2, high: 1 })).toBe('1 high · 2 watch');
    expect(severityLine({})).toBe('none');
  });
});

describe('Dashboard', () => {
  it('tells the story on one screen: alerts today, mix, states, model', async () => {
    render(<Dashboard />);
    expect(await screen.findByTestId('alerts-today')).toHaveProperty('textContent', '3');
    expect(screen.getByRole('img', { name: /Share of thermal sources by class over time/ })).toBeTruthy();
    expect(screen.getByTestId('top-states').textContent).toMatch(/Punjab/);
    expect(screen.getByTestId('top-states').textContent).toMatch(/120 ind\./);
    expect(screen.getByTestId('model-card').textContent).toMatch(/0\.993/);
    expect(screen.getByText(/2 open in the inbox/)).toBeTruthy();
  });
});
