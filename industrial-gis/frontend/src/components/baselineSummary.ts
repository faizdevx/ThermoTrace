import type { ThermalProperties } from '@/types/industrial';

export type Deviation = 'usual' | 'elevated' | 'far_above' | 'below' | 'new' | 'unknown';

export interface BaselineSummary {
  available: boolean;
  deviation: Deviation;
  headline: string;
  detail: string;
  today: string | null;
  monthly: (number | null)[];
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const MONTH_LABELS = MONTHS;

export function deviationFor(z: number | null | undefined): Deviation {
  if (z == null) return 'unknown';
  if (z >= 4) return 'far_above';
  if (z >= 2) return 'elevated';
  if (z <= -2) return 'below';
  return 'usual';
}

/** Plain-language comparison of a source with its own 365-day history. */
export function baselineSummary(p: Pick<ThermalProperties, 'baseline_scope' | 'baseline_known' | 'baseline_active_fraction'
  | 'baseline_covered_days' | 'baseline_frp_median' | 'baseline_frp_p95' | 'baseline_night_share' | 'baseline_monthly'
  | 'frp_today' | 'frp_z'>): BaselineSummary {
  const days = p.baseline_covered_days;
  if (p.baseline_scope == null || days == null || p.baseline_active_fraction == null) {
    return { available: false, deviation: 'unknown', headline: 'No baseline built yet',
      detail: 'Run the archive baseline (python -m src.thermal.baseline) to compare sources with their own history.',
      today: null, monthly: [] };
  }
  const where = p.baseline_scope === 'facility' ? 'this facility' : 'this 1 km cell';
  const today = p.frp_today != null && p.frp_z != null
    ? `${p.frp_today.toFixed(1)} MW on its latest day (${p.frp_z >= 0 ? '+' : ''}${p.frp_z.toFixed(1)} z)` : null;

  if (!p.baseline_known) {
    return { available: true, deviation: 'new',
      headline: `Never seen at ${where} in the last ${days} days`,
      detail: 'No VIIRS detection here in the baseline window, so this heat is new rather than a routine source.',
      today, monthly: [] };
  }
  const pct = Math.round(p.baseline_active_fraction * 100);
  const night = p.baseline_night_share != null ? `${Math.round(p.baseline_night_share * 100)}% of detections at night` : null;
  const typical = p.baseline_frp_median != null
    ? `typical ${p.baseline_frp_median.toFixed(1)} MW (95th percentile ${(p.baseline_frp_p95 ?? p.baseline_frp_median).toFixed(1)} MW)`
    : null;
  return {
    available: true, deviation: deviationFor(p.frp_z),
    headline: `Normally active ${pct}% of days at ${where}`,
    detail: [`over ${days} covered days`, typical, night].filter(Boolean).join(' · '),
    today, monthly: p.baseline_monthly ?? [],
  };
}
