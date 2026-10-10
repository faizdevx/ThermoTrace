import { useEffect, useState, type ReactNode } from 'react';
import { fetchThermalImagery } from '@/services/api';
import type { ThermalClassInfo, ThermalDetection, ThermalFeature, ThermalImagery } from '@/types/industrial';

const NA = 'Not available';

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div style={{ padding: '7px 0', borderBottom: '1px solid #f1f5f9' }}>
      <p style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#94a3b8', margin: '0 0 2px' }}>{k}</p>
      <div style={{ fontSize: 13, color: '#1e293b', fontWeight: 500, wordBreak: 'break-word' }}>{children}</div>
    </div>
  );
}

/** Tiny SVG timeline: one bar per detection, height = fire radiative power, night bars darker. */
function Timeline({ detections }: { detections: ThermalDetection[] }) {
  if (!detections.length) return null;
  const t0 = Date.parse(detections[0].time);
  const t1 = Date.parse(detections[detections.length - 1].time);
  const span = Math.max(t1 - t0, 3600_000);
  const maxFrp = Math.max(...detections.map(d => d.frp), 1);
  return (
    <svg viewBox="0 0 300 70" width="100%" role="img" aria-label="Detections over time (bar height = radiative power)">
      <line x1="0" y1="60" x2="300" y2="60" stroke="#e2e8f0" />
      {detections.map(d => {
        const x = 4 + ((Date.parse(d.time) - t0) / span) * 288;
        const h = 4 + (d.frp / maxFrp) * 50;
        return <rect key={d.detection_id} x={x} y={60 - h} width="3" height={h} fill={d.daynight === 'N' ? '#1e293b' : '#f59e0b'} />;
      })}
      <text x="0" y="69" fontSize="8" fill="#94a3b8">{new Date(t0).toLocaleDateString()}</text>
      <text x="300" y="69" fontSize="8" fill="#94a3b8" textAnchor="end">{new Date(t1).toLocaleDateString()}</text>
    </svg>
  );
}

type ImageryState = { status: 'loading' } | { status: 'error' } | { status: 'done'; data: ThermalImagery };

/** One Sentinel-2 chip pair for the open source, fetched when the source changes (cached server-side). */
function ImageryPanel({ sourceId }: { sourceId: string }) {
  const [state, setState] = useState<ImageryState>({ status: 'loading' });
  useEffect(() => {
    const controller = new AbortController();
    setState({ status: 'loading' });
    fetchThermalImagery(sourceId, controller.signal)
      .then(data => setState({ status: 'done', data }))
      .catch(() => { if (!controller.signal.aborted) setState({ status: 'error' }); });
    return () => controller.abort();
  }, [sourceId]);

  if (state.status === 'loading') return <span style={{ fontSize: 12, color: '#64748b' }}>Searching Sentinel-2 scenes…</span>;
  if (state.status === 'error') return <span style={{ fontSize: 12, color: '#b91c1c' }}>Satellite imagery service unavailable.</span>;
  const d = state.data;
  if (!d.available || !d.true_colour || !d.swir || !d.scene) return <span style={{ fontSize: 12, color: '#64748b' }}>{d.reason ?? NA}</span>;
  const figure = (src: string, caption: string) => (
    <figure style={{ margin: 0, flex: 1, position: 'relative' }}>
      <img src={src} alt={caption} style={{ width: '100%', display: 'block', borderRadius: 8 }} />
      <span aria-hidden style={{ position: 'absolute', left: '50%', top: '50%', width: 14, height: 14, marginLeft: -7, marginTop: -7,
                                 border: '1.5px solid #fff', borderRadius: '50%', boxShadow: '0 0 0 1px #0f172a' }} />
      <figcaption style={{ fontSize: 10.5, color: '#475569', marginTop: 3 }}>{caption}</figcaption>
    </figure>
  );
  return (
    <>
      <div style={{ display: 'flex', gap: 8 }}>
        {figure(d.true_colour, `True colour (B4/B3/B2) · ${d.window_km} km`)}
        {figure(d.swir, 'SWIR (B12/B11/B8A): flames and hot ground glow orange')}
      </div>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>
        {d.scene.platform ?? 'Sentinel-2'} · {new Date(d.scene.datetime).toLocaleString()} ({d.scene.offset_days > 0 ? '+' : ''}
        {d.scene.offset_days.toFixed(1)} d from last detection) · {d.scene.cloud_cover_percent.toFixed(0)}% scene cloud ·{' '}
        <code>{d.scene.id}</code>
      </div>
      <div style={{ fontSize: 10, color: '#94a3b8' }}>{d.attribution}</div>
    </>
  );
}

interface Props {
  feature: ThermalFeature | null;
  info: ThermalClassInfo | undefined;
  gone: boolean;
  onClose: () => void;
}

export function ThermalDetails({ feature, info, gone, onClose }: Props) {
  const p = feature?.properties;
  const open = feature !== null;
  const [lon, lat] = feature?.geometry.coordinates ?? [0, 0];
  const color = info?.color ?? '#64748b';
  return (
    <aside
      aria-label="Thermal source details"
      aria-hidden={!open}
      style={{
        position: 'fixed', top: 0, right: 0, width: 400, maxWidth: '100vw', height: '100vh', background: '#fff',
        boxShadow: '-6px 0 48px rgba(15,23,42,0.14)', zIndex: 620, overflowY: 'auto',
        transform: open ? 'translateX(0)' : 'translateX(100%)', transition: 'transform 0.32s cubic-bezier(0.4,0,0.2,1)',
        fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif",
      }}
    >
      <div style={{ background: `linear-gradient(135deg, #0f172a 0%, ${color}cc 140%)`, padding: '18px 20px', position: 'sticky', top: 0, zIndex: 1 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
          <div style={{ flex: 1 }}>
            <p style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.14em', color: '#fcd34d', margin: '0 0 4px' }}>Thermal source · NASA FIRMS</p>
            <h2 style={{ fontSize: 15, fontWeight: 700, color: '#f8fafc', margin: 0, lineHeight: 1.35 }}>{p?.thermal_label}</h2>
            <p style={{ fontSize: 12, color: '#cbd5e1', margin: '4px 0 0' }}>
              {p?.site_within && p.site_name ? p.site_name : 'No mapped facility'}{p?.state ? ` · ${p.state}` : ''}
            </p>
          </div>
          <button onClick={onClose} aria-label="Close thermal panel"
                  style={{ width: 32, height: 32, borderRadius: '50%', border: 'none', background: 'rgba(255,255,255,0.12)', color: '#fff', fontSize: 18, cursor: 'pointer' }}>×</button>
        </div>
      </div>

      {p && (
        <div style={{ padding: '10px 20px 28px' }}>
          {gone && (
            <div role="alert" style={{ margin: '4px 0 10px', padding: '8px 10px', borderRadius: 10, background: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b', fontSize: 12 }}>
              This source has no detections in the current window any more. Values are the last known ones.
            </div>
          )}
          {p.alert_level && (
            <div role="alert" style={{ margin: '4px 0 10px', padding: '9px 11px', borderRadius: 10, background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', fontSize: 12 }}>
              <strong style={{ textTransform: 'uppercase' }}>{p.alert_level} alert</strong> - {p.alert_text}
            </div>
          )}

          <Row k="Why this classification">
            <ul style={{ margin: '2px 0 0', paddingLeft: 18, fontSize: 12.5, fontWeight: 400, lineHeight: 1.5 }}>
              {p.reasons.map(r => <li key={r}>{r}</li>)}
            </ul>
            <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>
              Evidence margin {Math.round(p.class_margin * 100)} points → <strong>{p.class_confidence}</strong> separation from the next class.
              A heuristic score, not a verified cause.
            </div>
          </Row>

          <Row k="Class scores (share of evidence)">
            {Object.entries(p.class_scores).sort(([, a], [, b]) => b - a).slice(0, 4).map(([id, v]) => (
              <div key={id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, margin: '2px 0' }}>
                <span style={{ flex: '0 0 150px', color: '#475569' }}>{id.replace(/_/g, ' ')}</span>
                <div style={{ flex: 1, height: 6, background: '#e2e8f0', borderRadius: 9 }}>
                  <div style={{ width: `${Math.round(v * 100)}%`, height: '100%', background: id === p.thermal_class ? color : '#94a3b8', borderRadius: 9 }} />
                </div>
                <span style={{ width: 32, textAlign: 'right' }}>{Math.round(v * 100)}%</span>
              </div>
            ))}
          </Row>

          <Row k="Thermal behaviour">
            {p.n_detections} detections on {p.n_days} day(s) · radiative power max {p.frp_max.toFixed(1)} / mean {p.frp_mean.toFixed(1)} MW ·
            {' '}{Math.round(p.night_fraction * 100)}% at night · footprint {Math.round(p.extent_m)} m
            <div style={{ marginTop: 6 }}>{p.detections && <Timeline detections={p.detections} />}</div>
            <div style={{ fontSize: 10, color: '#94a3b8' }}>Dark bars = night passes, amber = day. Height = FRP.</div>
          </Row>

          <Row k="First / last detection">
            {new Date(p.first_seen).toLocaleString()} → {new Date(p.last_seen).toLocaleString()}
          </Row>

          <Row k="Nearest mapped facility (OpenStreetMap)">
            {p.site_name || p.site_ref ? (
              <>
                {p.site_name ?? p.site_ref} · {(p.site_kind ?? '').replace(/_/g, ' ')} ·{' '}
                {p.site_distance_m != null ? `${Math.round(p.site_distance_m)} m away` : NA}
                {p.site_within ? ' (inside its matching radius)' : ' (outside its matching radius - not treated as the source)'}
                {p.site_ref && <> · <a href={`https://www.openstreetmap.org/${p.site_ref}`} target="_blank" rel="noopener noreferrer" style={{ color: '#0369a1' }}>{p.site_ref}</a></>}
              </>
            ) : 'None within range'}
          </Row>

          <Row k="Land cover around the source (ESA WorldCover 2021)">
            {p.lc_dominant ? <>{p.lc_dominant} dominant · crop {Math.round((p.lc_crop ?? 0) * 100)}% · natural vegetation {Math.round((p.lc_vegetation ?? 0) * 100)}% · built {Math.round((p.lc_built ?? 0) * 100)}% · bare {Math.round((p.lc_bare ?? 0) * 100)}%</> : NA}
          </Row>

          <Row k="Satellite imagery (Sentinel-2, clearest scene within 5 days)">
            <ImageryPanel sourceId={p.source_id} />
          </Row>

          <Row k="Location">{lat.toFixed(5)}, {lon.toFixed(5)} · instruments: {p.instruments.replace(/VIIRS_|_NRT/g, '')}</Row>
          <Row k="Source ID"><code style={{ fontSize: 11 }}>{p.source_id}</code></Row>
          <p style={{ fontSize: 10.5, color: '#94a3b8', marginTop: 10, lineHeight: 1.45 }}>
            Thermal anomalies are detected by NASA VIIRS at 375 m. A detection shows heat, not its cause: confirm industrial incidents with
            high-resolution imagery or ground reports. Data: NASA FIRMS, © OpenStreetMap contributors (ODbL), ESA WorldCover (CC BY 4.0).
          </p>
        </div>
      )}
    </aside>
  );
}
