import type { AlertDecision, AlertSeverity, AlertStatus, ThermalAlert } from '@/types/industrial';

export const SEVERITY_COLOR: Record<AlertSeverity, string> = { watch: '#d97706', high: '#ea580c', critical: '#dc2626' };
export const STATUS_LABEL: Record<AlertStatus, string> = { open: 'Open', confirmed: 'Confirmed fire', rejected: 'Rejected', unsure: 'Unsure' };
export const DECISION_LABEL: Record<AlertDecision, string> = { confirmed: 'Confirm', rejected: 'Reject', unsure: 'Unsure' };

export interface AlertRow {
  id: number;
  title: string;
  meta: string;
  color: string;
  status: string;
  isOpen: boolean;
  test: boolean;
  channels: string;
}

/** What the inbox row says. Missing facts stay out; nothing is guessed. */
export function alertRow(a: ThermalAlert): AlertRow {
  const kind = a.site_kind ? a.site_kind.replace(/_/g, ' ') : null;
  const place = a.site_name || (a.site_ref && kind ? `Unnamed ${kind}` : 'Unmapped location');
  const power = a.peak_frp ?? a.frp_max;
  const bits = [kind, a.state, power != null ? `${power.toFixed(0)} MW` : null,
    a.p_fire != null && a.trigger !== 'frp_z' ? `P(fire) ${Math.round(a.p_fire * 100)}%` : null,
    a.frp_z != null && a.trigger !== 'p_fire' ? `${a.frp_z >= 0 ? '+' : ''}${a.frp_z.toFixed(1)} sd vs own baseline` : null,
  ].filter(Boolean);
  const sent = Object.entries(a.delivery ?? {});
  return {
    id: a.alert_id,
    title: `${a.is_test ? '[TEST] ' : ''}${place}`,
    meta: bits.join(' · '),
    color: SEVERITY_COLOR[a.severity],
    status: STATUS_LABEL[a.status],
    isOpen: a.status === 'open',
    test: a.is_test,
    channels: sent.length ? sent.map(([c, d]) => `${c} ${d.ok ? 'sent' : 'FAILED'}`).join(', ') : 'not delivered (no channel configured)',
  };
}

export function openCount(alerts: ThermalAlert[]): number {
  return alerts.filter(a => a.status === 'open' && !a.is_test).length;
}

export function decisionHint(enabled: boolean, token: string): string | null {
  if (!enabled) return 'Decisions are disabled on this server (ANALYST_TOKEN is not set).';
  if (!token.trim()) return 'Enter the analyst token to record a decision.';
  return null;
}
