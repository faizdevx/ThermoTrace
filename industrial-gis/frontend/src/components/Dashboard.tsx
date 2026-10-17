import { useEffect, useState } from 'react';
import { fetchDashboard } from '@/services/api';
import { CLASS_ORDER, modelStory, pct, severityLine, stackedMix } from '@/components/dashboardView';
import type { DashboardData } from '@/types/industrial';

const COLORS: Record<string, string> = {
  industrial_persistent: '#dc2626', industrial_fire_candidate: '#f97316', agricultural_burning: '#eab308',
  vegetation_fire: '#16a34a', unmapped_persistent: '#7c3aed', other: '#64748b',
};
const LABELS: Record<string, string> = {
  industrial_persistent: 'Persistent industrial', industrial_fire_candidate: 'Possible industrial fire', agricultural_burning: 'Crop burning',
  vegetation_fire: 'Forest / vegetation', unmapped_persistent: 'Persistent, unmapped', other: 'Other',
};

const card: React.CSSProperties = { background: 'rgba(255,255,255,0.92)', borderRadius: 14, padding: '14px 16px', boxShadow: '0 1px 4px rgba(15,23,42,0.08)' };
const h: React.CSSProperties = { margin: '0 0 8px', fontSize: 12, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase', color: '#64748b' };

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div style={{ ...card, flex: '1 1 190px' }}>
      <p style={h}>{label}</p>
      <p style={{ margin: 0, fontSize: 30, fontWeight: 800, color: tone ?? '#0f172a', lineHeight: 1.1 }}>{value}</p>
      {sub && <p style={{ margin: '4px 0 0', fontSize: 12, color: '#64748b' }}>{sub}</p>}
    </div>
  );
}

function ClassMix({ data }: { data: DashboardData }) {
  const s = stackedMix(data.class_mix);
  if (!s) {
    return <p style={{ fontSize: 13, color: '#64748b' }}>Not enough history yet: the class mix is drawn from one point per published FIRMS batch ({data.class_mix.length} so far).</p>;
  }
  const W = 560, H = 190, L = 6, B = 22;
  const t0 = s.times[0], t1 = s.times[s.times.length - 1];
  const x = (t: number) => L + ((t - t0) / Math.max(t1 - t0, 1)) * (W - L - 6);
  const y = (v: number) => 8 + (1 - v) * (H - B - 8);
  const layers = s.classes.map((c, k) => {
    const top = s.tops[k].map((v, i) => `${x(s.times[i]).toFixed(1)},${y(v).toFixed(1)}`);
    const below = k === 0 ? s.times.map(t => `${x(t).toFixed(1)},${y(0).toFixed(1)}`) : s.tops[k - 1].map((v, i) => `${x(s.times[i]).toFixed(1)},${y(v).toFixed(1)}`);
    return <polygon key={c} points={[...top, ...below.reverse()].join(' ')} fill={COLORS[c] ?? '#64748b'} opacity={0.85}><title>{LABELS[c] ?? c}</title></polygon>;
  });
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Share of thermal sources by class over time">
        {layers}
        <text x={L} y={H - 6} fontSize="10" fill="#64748b">{new Date(t0).toLocaleDateString()}</text>
        <text x={W - 6} y={H - 6} fontSize="10" fill="#64748b" textAnchor="end">{new Date(t1).toLocaleDateString()}</text>
      </svg>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', fontSize: 12, color: '#475569' }}>
        {s.classes.map(c => <span key={c}><span style={{ display: 'inline-block', width: 9, height: 9, borderRadius: 2, background: COLORS[c] ?? '#64748b', marginRight: 4 }} />{LABELS[c] ?? c}</span>)}
      </div>
    </div>
  );
}

function TopStates({ data }: { data: DashboardData }) {
  const max = Math.max(1, ...data.top_states.map(s => s.sources));
  return (
    <div data-testid="top-states">
      {data.top_states.map(s => (
        <div key={s.state} style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '5px 0', fontSize: 12 }}>
          <span style={{ width: 118, flexShrink: 0, color: '#334155', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.state}</span>
          <span style={{ flex: 1, background: '#f1f5f9', borderRadius: 4, height: 12, position: 'relative' }}>
            <span style={{ position: 'absolute', inset: 0, width: `${(s.sources / max) * 100}%`, background: '#cbd5e1', borderRadius: 4 }} />
            <span style={{ position: 'absolute', inset: 0, width: `${(s.industrial / max) * 100}%`, background: '#dc2626', borderRadius: 4 }} />
          </span>
          <span style={{ width: 92, textAlign: 'right', color: '#64748b' }}>{s.sources.toLocaleString()} · <span style={{ color: '#b91c1c' }}>{s.industrial} ind.</span></span>
        </div>
      ))}
    </div>
  );
}

function ModelCard({ m }: { m: NonNullable<DashboardData['model']> }) {
  const rows: [string, number | null, [number, number] | null, string][] = [
    ['Trained model', m.macro_f1.model, m.macro_f1_ci.model, '#0f172a'], ['Rule classifier', m.macro_f1.rules, m.macro_f1_ci.rules, '#94a3b8'],
    ['Thermal behaviour only', m.macro_f1.blind, m.macro_f1_ci.blind, '#f59e0b'],
  ];
  return (
    <div data-testid="model-card">
      <p style={{ margin: '0 0 8px', fontSize: 12, color: '#64748b' }}>{m.model_version} · macro-F1 on held-out regions and months</p>
      {rows.map(([name, v, ci, color]) => (
        <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '6px 0', fontSize: 12 }}>
          <span style={{ width: 150, color: '#334155' }}>{name}</span>
          <span style={{ flex: 1, background: '#f1f5f9', borderRadius: 4, height: 14, position: 'relative' }}>
            <span style={{ position: 'absolute', inset: 0, width: `${(v ?? 0) * 100}%`, background: color, borderRadius: 4 }} />
          </span>
          <span style={{ width: 120, textAlign: 'right', fontWeight: 700 }}>{v?.toFixed(3) ?? 'n/a'}{ci ? <span style={{ fontWeight: 400, color: '#64748b' }}> ({ci[0].toFixed(2)}-{ci[1].toFixed(2)})</span> : null}</span>
        </div>
      ))}
      <p style={{ margin: '8px 0 0', fontSize: 12, color: '#475569', lineHeight: 1.45 }}>{modelStory(m)}</p>
    </div>
  );
}

export function Dashboard({ onOpenInbox }: { onOpenInbox?: () => void }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const load = () => fetchDashboard(controller.signal).then(d => { setData(d); setError(null); })
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Dashboard unavailable'); });
    load();
    const id = setInterval(load, 60_000);
    return () => { controller.abort(); clearInterval(id); };
  }, []);

  if (error && !data) return <p role="alert" style={{ color: '#b91c1c' }}>Dashboard unavailable: {error}</p>;
  if (!data) return <p style={{ color: '#64748b' }}>Loading the dashboard…</p>;
  const ind = (data.by_class.industrial_persistent ?? 0) + (data.by_class.industrial_fire_candidate ?? 0);
  const crop = data.by_class.agricultural_burning ?? 0;
  return (
    <div data-testid="dashboard" style={{ display: 'grid', gap: 12 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        <div style={{ ...card, flex: '1 1 190px', cursor: onOpenInbox ? 'pointer' : undefined }} onClick={onOpenInbox} role={onOpenInbox ? 'button' : undefined}>
          <p style={h}>Alerts today</p>
          <p data-testid="alerts-today" style={{ margin: 0, fontSize: 30, fontWeight: 800, color: data.alerts.today ? '#dc2626' : '#0f172a', lineHeight: 1.1 }}>{data.alerts.today}</p>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: '#64748b' }}>{severityLine(data.alerts.today_by_severity)} · {data.alerts.open} open in the inbox</p>
        </div>
        <Stat label="Thermal sources" value={data.totals.sources.toLocaleString()} sub={`${data.totals.detections.toLocaleString()} VIIRS detections`} />
        <Stat label="Industrial heat" value={ind.toLocaleString()} sub={`${((ind / Math.max(data.totals.sources, 1)) * 100).toFixed(0)}% of sources; crop burning is ${crop.toLocaleString()}`} tone="#b91c1c" />
        <Stat label="Newest detection" value={data.totals.newest_detection ? new Date(data.totals.newest_detection).toISOString().slice(0, 10) : 'n/a'} sub={data.totals.newest_detection ? new Date(data.totals.newest_detection).toISOString().slice(11, 16) + ' UTC' : undefined} />
      </div>
      <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))' }}>
        <div style={card}><p style={h}>Class mix over time</p><ClassMix data={data} /></div>
        <div style={card}><p style={h}>Top states by thermal sources <span style={{ textTransform: 'none', letterSpacing: 0 }}>(red = industrial)</span></p><TopStates data={data} /></div>
      </div>
      <div style={card}>
        <p style={h}>Model</p>
        {data.model ? <ModelCard m={data.model} /> : <p style={{ fontSize: 13, color: '#64748b' }}>No trained model registered.</p>}
      </div>
      {data.alerts.note && <p style={{ fontSize: 12, color: '#64748b' }}>{data.alerts.note}</p>}
    </div>
  );
}

export { CLASS_ORDER };
