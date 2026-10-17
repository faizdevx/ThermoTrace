import { useState } from 'react';
import { API_BASE, postAlertDecision } from '@/services/api';
import { alertRow, decisionHint, DECISION_LABEL, SEVERITY_COLOR, STATUS_LABEL } from '@/components/alertView';
import type { AlertsState } from '@/hooks/useAlerts';
import { useAuth } from '@/hooks/useAuth';
import type { AlertDecision, ThermalAlert } from '@/types/industrial';

function stored(key: string, area: 'session' | 'local'): string {
  try { return (area === 'session' ? sessionStorage : localStorage).getItem(key) ?? ''; } catch { return ''; }
}
function store(key: string, value: string, area: 'session' | 'local') {
  try { (area === 'session' ? sessionStorage : localStorage).setItem(key, value); } catch { /* private mode: keep in memory only */ }
}

const btn = (bg: string, disabled: boolean): React.CSSProperties => ({
  flex: 1, padding: '8px 6px', borderRadius: 8, border: 'none', background: disabled ? '#cbd5e1' : bg, color: '#fff',
  fontWeight: 700, fontSize: 13, cursor: disabled ? 'not-allowed' : 'pointer',
});

function Decide({ alert, enabled, demo, onDone }: { alert: ThermalAlert; enabled: boolean; demo?: boolean; onDone: () => void }) {
  const [token, setToken] = useState(() => stored('tt-analyst-token', 'session'));
  const [analyst, setAnalyst] = useState(() => stored('tt-analyst-name', 'local'));
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const auth = useAuth();
  const hint = auth.enforced ? null : decisionHint(enabled, token);
  if (auth.enforced && !auth.can('analyst')) {
    return <p data-testid="role-hint" style={{ marginTop: 10, padding: 10, borderRadius: 10, background: '#f1f5f9', color: '#475569', fontSize: 12 }}>
      You are signed in as <strong>{auth.user?.username}</strong> ({auth.user?.role}). Recording a decision needs the analyst role.</p>;
  }
  if (demo) {
    return <p data-testid="demo-readonly" style={{ marginTop: 10, padding: 10, borderRadius: 10, background: '#f5f3ff', color: '#5b21b6', fontSize: 12 }}>Public read-only demo: analyst decisions (Confirm / Reject / Unsure) are disabled here. Run the stack yourself to record decisions into <code>labels</code>.</p>;
  }

  const send = async (d: AlertDecision) => {
    setBusy(true);
    setMsg(null);
    try {
      store('tt-analyst-token', token, 'session');
      store('tt-analyst-name', analyst, 'local');
      const r = await postAlertDecision(alert.alert_id, d, note, auth.enforced ? (auth.user?.username ?? '') : analyst, auth.enforced ? '' : token);
      setMsg({ ok: true, text: `Recorded: ${DECISION_LABEL[d]}. Written to labels as "${r.label}" (${r.label_id}).` });
      setNote('');
      onDone();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : 'Could not save the decision' });
    } finally {
      setBusy(false);
    }
  };
  const disabled = busy || hint !== null;
  const field: React.CSSProperties = { width: '100%', boxSizing: 'border-box', padding: '6px 8px', borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 12, marginBottom: 6 };
  return (
    <div data-testid="decide" style={{ marginTop: 10, padding: 10, border: '1px solid #e2e8f0', borderRadius: 10 }}>
      {auth.enforced
        ? <p data-testid="signed-in-as" style={{ margin: '0 0 6px', fontSize: 12, color: '#475569' }}>Decision recorded as <strong>{auth.user?.username}</strong> ({auth.user?.role}), logged in the audit trail.</p>
        : null}
      <div style={{ display: auth.enforced ? 'none' : 'flex', gap: 6 }}>
        <input aria-label="Analyst name" placeholder="Your name" value={analyst} onChange={e => setAnalyst(e.target.value)} style={field} />
        <input aria-label="Analyst token" placeholder="Analyst token" type="password" value={token} onChange={e => setToken(e.target.value)} style={field} />
      </div>
      <textarea aria-label="Note" placeholder="Note (what did you check?)" value={note} onChange={e => setNote(e.target.value)} rows={2} style={{ ...field, resize: 'vertical' }} />
      <div style={{ display: 'flex', gap: 6 }}>
        <button disabled={disabled} onClick={() => send('confirmed')} style={btn('#dc2626', disabled)}>Confirm fire</button>
        <button disabled={disabled} onClick={() => send('rejected')} style={btn('#475569', disabled)}>Reject</button>
        <button disabled={disabled} onClick={() => send('unsure')} style={btn('#7c3aed', disabled)}>Unsure</button>
      </div>
      {hint && <p data-testid="decide-hint" style={{ fontSize: 12, color: '#92400e', margin: '6px 0 0' }}>{hint}</p>}
      {msg && <p role="status" style={{ fontSize: 12, margin: '6px 0 0', color: msg.ok ? '#166534' : '#b91c1c' }}>{msg.text}</p>}
    </div>
  );
}

function Detail({ a, enabled, demo, onLocate, onDone }: { a: ThermalAlert; enabled: boolean; demo?: boolean; onLocate: (a: ThermalAlert) => void; onDone: () => void }) {
  const row = alertRow(a);
  const auth = useAuth();
  return (
    <div data-testid="alert-detail">
      <p style={{ margin: '0 0 4px', fontSize: 13, color: '#334155' }}>{a.summary}</p>
      <p style={{ margin: '0 0 6px', fontSize: 12, color: '#64748b' }}>Delivery: {row.channels}</p>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <button onClick={() => onLocate(a)} style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid #cbd5e1', background: '#fff', fontSize: 12, cursor: 'pointer' }}>
          Show on map + satellite imagery
        </button>
        {auth.can('analyst') && <a data-testid="report-link" href={`${API_BASE}/alerts/${a.alert_id}/report.pdf`} download
          style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid #0f172a', background: '#0f172a', color: '#fff', fontSize: 12, textDecoration: 'none', fontWeight: 600 }}>
          Download PDF report
        </a>}
      </div>
      <ol data-testid="alert-timeline" style={{ listStyle: 'none', padding: 0, margin: '10px 0 0', borderLeft: '2px solid #e2e8f0' }}>
        {(a.timeline ?? []).map((e, i) => (
          <li key={i} style={{ padding: '3px 0 3px 10px', fontSize: 12 }}>
            <span style={{ color: '#64748b', fontSize: 12, display: 'block' }}>{new Date(e.at).toLocaleString()}</span>
            <span style={{ fontWeight: e.kind === 'decision' || e.kind === 'peak' ? 700 : 500 }}>{e.text}</span>
          </li>
        ))}
      </ol>
      {a.is_test ? <p style={{ fontSize: 12, color: '#64748b' }}>Test alert: decisions on it are still written to labels (flagged is_test).</p> : null}
      <Decide key={a.alert_id} alert={a} enabled={enabled} demo={demo} onDone={onDone} />
    </div>
  );
}

export function AlertInbox({ alerts, open, onClose, onLocate }: {
  alerts: AlertsState; open: boolean; onClose: () => void; onLocate: (a: ThermalAlert) => void;
}) {
  if (!open) return null;
  const { detail, selectedId } = alerts;
  return (
    <aside aria-label="Alert inbox" style={{
      position: 'fixed', top: 0, right: 0, bottom: 0, width: 'min(420px, 100vw)', zIndex: 700, background: '#fff',
      boxShadow: '-6px 0 24px rgba(15,23,42,0.18)', display: 'flex', flexDirection: 'column',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', padding: '12px 14px', borderBottom: '1px solid #e2e8f0' }}>
        <strong style={{ flex: 1, fontSize: 15 }}>Alert inbox</strong>
        <button aria-label="Close inbox" onClick={onClose} style={{ border: 'none', background: 'none', fontSize: 20, cursor: 'pointer' }}>×</button>
      </div>
      <div style={{ padding: '6px 14px', fontSize: 12, color: '#64748b' }}>
        {alerts.available
          ? `Delivery channels: ${alerts.channels.length ? alerts.channels.join(', ') : 'none configured'}`
          : 'Alerts need the PostGIS stack (not available in this mode).'}
        {alerts.error ? <span style={{ color: '#b91c1c' }}> · {alerts.error}</span> : null}
      </div>
      <div style={{ overflowY: 'auto', flex: 1, padding: '0 14px 14px' }}>
        {alerts.available && alerts.alerts.length === 0 && <p style={{ fontSize: 13, color: '#64748b' }}>No alerts yet. A source becomes an alert when it looks like an industrial fire or runs far above its facility's own baseline.</p>}
        {alerts.alerts.map(a => {
          const row = alertRow(a);
          const active = selectedId === a.alert_id;
          return (
            <div key={a.alert_id} style={{ borderBottom: '1px solid #f1f5f9' }}>
              <button
                onClick={() => alerts.select(active ? null : a.alert_id)}
                aria-expanded={active}
                style={{ display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none', padding: '9px 0', cursor: 'pointer' }}
              >
                <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span style={{ background: SEVERITY_COLOR[a.severity], color: '#fff', fontSize: 12, fontWeight: 700, borderRadius: 6, padding: '1px 6px', textTransform: 'uppercase' }}>{a.severity}</span>
                  <span style={{ fontWeight: 600, fontSize: 13, flex: 1 }}>{row.title}</span>
                  <span style={{ fontSize: 12, color: row.isOpen ? '#b45309' : '#64748b', fontWeight: 600 }}>{STATUS_LABEL[a.status]}</span>
                </span>
                <span style={{ display: 'block', fontSize: 12, color: '#64748b', marginTop: 2 }}>{row.meta}</span>
              </button>
              {active && detail && detail.alert_id === a.alert_id && (
                <Detail a={detail} enabled={alerts.decisionsEnabled} demo={alerts.demoReadonly} onLocate={onLocate} onDone={alerts.refresh} />
              )}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
