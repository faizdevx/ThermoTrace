import type { DashboardData } from '@/types/industrial';

export const CLASS_ORDER = ['industrial_fire_candidate', 'industrial_persistent', 'unmapped_persistent', 'agricultural_burning', 'vegetation_fire', 'other'];

export interface StackedSeries {
  times: number[];
  classes: string[];
  /** cumulative top edge per class (same order as ``classes``), as shares 0..1 of the total at each time */
  tops: number[][];
  totals: number[];
}

/** Class mix over time as stacked shares; null when there are fewer than two points to draw. */
export function stackedMix(rows: DashboardData['class_mix']): StackedSeries | null {
  if (rows.length < 2) return null;
  const classes = CLASS_ORDER.filter(c => rows.some(r => (r.by_class[c] ?? 0) > 0));
  for (const r of rows) for (const c of Object.keys(r.by_class)) if (!classes.includes(c)) classes.push(c);
  const totals = rows.map(r => Object.values(r.by_class).reduce((a, b) => a + b, 0));
  const tops = classes.map(() => rows.map(() => 0));
  rows.forEach((r, i) => {
    let acc = 0;
    classes.forEach((c, k) => {
      acc += totals[i] > 0 ? (r.by_class[c] ?? 0) / totals[i] : 0;
      tops[k][i] = acc;
    });
  });
  return { times: rows.map(r => Date.parse(r.at)), classes, tops, totals };
}

export function pct(v: number | null | undefined, digits = 1): string {
  return v == null ? 'n/a' : `${(v * 100).toFixed(digits)}%`;
}

/** The sentence under the model card: what the number does and does not say. */
export function modelStory(m: NonNullable<DashboardData['model']>): string {
  const inc = m.incidents;
  const rec = inc['model_recall_of_detected_at_0.25_false_alarms_per_day'];
  const parts = [`On ${m.test_units ?? '?'} held-out units the trained model scores macro-F1 ${m.macro_f1.model?.toFixed(2)} against ${m.macro_f1.rules?.toFixed(2)} for the rules`,
    `but a model that sees only thermal behaviour scores ${m.macro_f1.blind?.toFixed(2)}`];
  if (inc.with_detection != null) {
    parts.push(`Of ${inc.verified} verified industrial fires VIIRS saw ${inc.with_detection}; the model flags ${rec != null ? pct(rec, 0) : 'n/a'} of those at 0.25 false alarms per day (rules: ${pct(inc.rules_recall_of_detected, 0)})`);
  }
  return parts.join('; ') + '. Labels are rule-derived, so the first number is agreement with the rules.';
}

export function severityLine(by: Record<string, number>): string {
  const order = ['critical', 'high', 'watch'];
  const bits = order.filter(k => by[k]).map(k => `${by[k]} ${k}`);
  return bits.length ? bits.join(' · ') : 'none';
}
