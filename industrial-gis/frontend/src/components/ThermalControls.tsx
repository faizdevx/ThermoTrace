import { useState } from 'react';
import { thermalExportUrl } from '@/services/api';
import { WINDOWS, type useThermal } from '@/hooks/useThermal';
import { formatAge } from '@/components/LiveStatus';
import { useNarrow } from '@/hooks/useNarrow';
import { useAuth } from '@/hooks/useAuth';

type ThermalState = ReturnType<typeof useThermal>;

const box: React.CSSProperties = {
  position: 'absolute', top: 12, left: 12, zIndex: 500, width: 270, maxWidth: 'calc(100% - 24px)',
  background: 'rgba(255,255,255,0.95)', backdropFilter: 'blur(10px)', borderRadius: 14,
  boxShadow: '0 2px 16px rgba(15,23,42,0.16)', fontFamily: "'IBM Plex Sans', sans-serif", fontSize: 12,
  maxHeight: 'calc(100% - 90px)', overflowY: 'auto',
};

export function ThermalControls({ t }: { t: ThermalState }) {
  const narrow = useNarrow();
  const auth = useAuth();
  const [open, setOpen] = useState(!narrow);      // phones start with the panel folded so the map is visible
  const age = t.status?.freshness?.latest_detection_age_seconds ?? null;
  const fresh = t.status?.freshness?.state;
  const alerts = t.stats ? t.stats.alerts.watch + t.stats.alerts.high + t.stats.alerts.critical : 0;

  return (
    <div style={box} id="thermal-controls">
      <button
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '9px 12px', border: 'none',
                 background: 'transparent', cursor: 'pointer', textAlign: 'left', fontWeight: 700, color: '#0f172a' }}
      >
        <span style={{ fontSize: 15 }}>🔥</span>
        <span style={{ flex: 1 }}>Thermal sources (NASA FIRMS)</span>
        {alerts > 0 && <span style={{ background: '#dc2626', color: '#fff', borderRadius: 99, padding: '1px 8px', fontSize: 10 }}>{alerts} alert{alerts > 1 ? 's' : ''}</span>}
        <span>{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div style={{ padding: '0 12px 12px' }}>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer', marginBottom: 6 }}>
            <input id="thermal-toggle" type="checkbox" checked={t.enabled} onChange={e => t.setEnabled(e.target.checked)} />
            Show thermal layer
          </label>
          {t.error && <p role="alert" style={{ color: '#b91c1c', margin: '4px 0' }}>Thermal data unavailable: {t.error}</p>}
          {t.enabled && !t.error && (
            <>
              <p style={{ margin: '0 0 6px', color: '#64748b' }}>
                {t.stats ? <><strong style={{ color: '#0f172a' }}>{t.stats.total_sources.toLocaleString()}</strong> sources from{' '}
                  {t.stats.detections.toLocaleString()} VIIRS detections</> : 'Loading…'}
                {age != null && fresh !== 'replay' && <> · newest {formatAge(age)} old</>}
                {fresh && <span style={{ marginLeft: 6, color: fresh === 'live' ? '#15803d' : fresh === 'replay' ? '#6d28d9' : '#b45309', fontWeight: 700 }}>
                  {fresh === 'live' ? '● live' : fresh === 'replay' ? '● replay' : '● delayed'}</span>}
              </p>
              <div role="group" aria-label="Thermal classes">
                {t.classes.map(c => (
                  <label key={c.id} title={c.description} style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer', padding: '2px 0' }}>
                    <input type="checkbox" checked={t.active.has(c.id)} onChange={() => t.toggleClass(c.id)} />
                    <span style={{ width: 11, height: 11, borderRadius: '50%', background: c.color, flexShrink: 0 }} />
                    <span style={{ flex: 1 }}>{c.label}</span>
                    <span style={{ color: '#64748b' }}>{t.stats?.by_class[c.id]?.toLocaleString() ?? 0}</span>
                  </label>
                ))}
              </div>
              <div style={{ marginTop: 8, display: 'grid', gap: 6 }}>
                <label>
                  Activity window{' '}
                  <select id="thermal-window" value={t.windowIdx} onChange={e => t.setWindowIdx(Number(e.target.value))}>
                    {WINDOWS.map((w, i) => <option key={w.label} value={i}>{w.label}</option>)}
                  </select>
                </label>
                <label>
                  Min. power: <strong>{t.minFrp} MW</strong>
                  <input id="thermal-minfrp" type="range" min={0} max={100} step={5} value={t.minFrp}
                         onChange={e => t.setMinFrp(Number(e.target.value))} style={{ width: '100%' }} />
                </label>
                <label style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
                  <input id="thermal-alerts" type="checkbox" checked={t.alertsOnly} onChange={e => t.setAlertsOnly(e.target.checked)} />
                  Alerts only
                </label>
              </div>
              {!auth.can('analyst') ? <p style={{ margin: '8px 0 0', color: '#94a3b8' }}>Downloads need the analyst role.</p> : null}
              <div style={{ marginTop: 8, display: auth.can('analyst') ? 'flex' : 'none', gap: 6, alignItems: 'center' }}>
                <span style={{ color: '#64748b' }}>Export:</span>
                {(['csv', 'geojson'] as const).map(f => (
                  <a key={f} href={thermalExportUrl(f, t.filters)} download
                     style={{ padding: '2px 9px', border: '1px solid #cbd5e1', borderRadius: 7, color: '#0f172a', textDecoration: 'none', fontWeight: 600 }}>
                    {f === 'csv' ? 'CSV' : 'GeoJSON'}
                  </a>
                ))}
              </div>
              <p style={{ margin: '8px 0 0', fontSize: 10, color: '#94a3b8', lineHeight: 1.4 }}>
                Classes are heuristic evidence scores, not confirmed causes. Data: NASA FIRMS VIIRS (~3 h latency), OpenStreetMap, ESA WorldCover.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
