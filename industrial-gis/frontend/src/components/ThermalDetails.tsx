import { useEffect, useState, type ReactNode } from 'react';
import { fetchThermalImagery } from '@/services/api';
import { baselineSummary, MONTH_LABELS, type Deviation } from '@/components/baselineSummary';
import { modelOpinion } from '@/components/modelOpinion';
import { imageryEvidence } from '@/components/imageryEvidence';
import type { ThermalClassInfo, ThermalDetection, ThermalFeature, ThermalImagery, ThermalProperties } from '@/types/industrial';

const NA = 'Not available';

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div style={{ padding: '7px 0', borderBottom: '1px solid #f1f5f9' }}>
      <p style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#64748b', margin: '0 0 2px' }}>{k}</p>
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

  if (state.status === 'loading') return <span style={{ fontSize: 12, color: '#64748b' }}>Searching Sentinel-2 and Landsat scenes (up to 30 s the first time)…</span>;
  if (state.status === 'error') return <span style={{ fontSize: 12, color: '#b91c1c' }}>Satellite imagery service unavailable.</span>;
  const e = imageryEvidence(state.data);
  const figure = (key: string, src: string, caption: string) => (
    <figure key={key} style={{ margin: 0, flex: '1 1 45%', position: 'relative', minWidth: 120 }}>
      <img src={src} alt={caption} style={{ width: '100%', display: 'block', borderRadius: 8 }} />
      <span aria-hidden style={{ position: 'absolute', left: '50%', top: '38%', width: 14, height: 14, marginLeft: -7, marginTop: -7,
                                 border: '1.5px solid #fff', borderRadius: '50%', boxShadow: '0 0 0 1px #0f172a' }} />
      <figcaption style={{ fontSize: 12.5, color: '#475569', marginTop: 3 }}>{caption}</figcaption>
    </figure>
  );
  const tone = e.dnbr?.tone === 'high' ? { bg: '#fef2f2', fg: '#b91c1c' } : e.dnbr?.tone === 'low' ? { bg: '#fffbeb', fg: '#b45309' }
    : { bg: '#f1f5f9', fg: '#475569' };
  return (
    <>
      {!e.available && <span style={{ fontSize: 12, color: '#64748b' }}>{e.reason}</span>}
      {e.panels.length > 0 && (
        <div data-testid="imagery-panels" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {e.panels.map(p => figure(p.key, p.src, p.title))}
        </div>
      )}
      {e.afterLine && <div data-testid="imagery-scene" style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>{e.afterLine}</div>}
      {e.beforeLine && <div style={{ fontSize: 12, color: '#64748b' }}>{e.beforeLine}</div>}
      {e.available && !e.dnbr && (
        <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>No clear earlier scene or too much cloud over the chip: no dNBR.</div>
      )}
      {e.dnbr && (
        <div data-testid="imagery-dnbr" style={{ marginTop: 5, fontSize: 12, padding: '4px 8px', borderRadius: 8, background: tone.bg,
                                                   color: tone.fg, fontWeight: 600, display: 'inline-block' }}>
          dNBR (p95) {e.dnbr.value} - {e.dnbr.severity}; changed like a burn scar over {e.dnbr.burned}
        </div>
      )}
      {e.hotPixels && <div data-testid="imagery-hot" style={{ fontSize: 12, marginTop: 4 }}>{e.hotPixels}</div>}
      {e.landsat && <div data-testid="imagery-landsat" style={{ fontSize: 12, marginTop: 2, color: '#334155' }}>{e.landsat}</div>}
      <div style={{ fontSize: 12, color: '#64748b', marginTop: 3 }}>
        {state.data.attribution ?? 'Contains modified Copernicus Sentinel data (ESA); Landsat courtesy of USGS.'}
      </div>
    </>
  );
}

interface Props {
  feature: ThermalFeature | null;
  info: ThermalClassInfo | undefined;
  gone: boolean;
  onClose: () => void;
}

const DEVIATION_STYLE: Record<Deviation, { bg: string; fg: string; label: string }> = {
  usual: { bg: '#ecfdf5', fg: '#047857', label: 'within its usual range' },
  elevated: { bg: '#fffbeb', fg: '#b45309', label: 'elevated versus its own baseline' },
  far_above: { bg: '#fef2f2', fg: '#b91c1c', label: 'far above its own baseline' },
  below: { bg: '#f1f5f9', fg: '#475569', label: 'below its usual level' },
  new: { bg: '#fef2f2', fg: '#b91c1c', label: 'new heat source' },
  unknown: { bg: '#f1f5f9', fg: '#475569', label: 'no comparison available' },
};

function ModelRow({ p }: { p: ThermalProperties }) {
  const m = modelOpinion(p);
  const tone = m.agreement === 'agrees' ? '#047857' : m.agreement === 'differs' ? '#b45309' : '#475569';
  return (
    <Row k="Trained model (second opinion)">
      <div data-testid="model-headline" style={{ fontWeight: 600 }}>{m.headline}</div>
      <div data-testid="model-agreement" style={{ fontSize: 12, color: tone, fontWeight: 500 }}>{m.note}</div>
      {m.bars.map(b => (
        <div key={b.id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, margin: '2px 0' }}>
          <span style={{ flex: '0 0 170px', color: '#475569' }}>{b.label}</span>
          <div style={{ flex: 1, height: 6, background: '#e2e8f0', borderRadius: 9 }}>
            <div style={{ width: `${Math.round(b.p * 100)}%`, height: '100%', background: '#0369a1', borderRadius: 9 }} />
          </div>
          <span style={{ width: 32, textAlign: 'right' }}>{Math.round(b.p * 100)}%</span>
        </div>
      ))}
      {m.reasons.length > 0 && (
        <ul data-testid="model-reasons" style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12, fontWeight: 400, lineHeight: 1.45 }}>
          {m.reasons.map(r => <li key={r}>{r}</li>)}
        </ul>
      )}
      {m.available && (
        <div style={{ fontSize: 12, color: '#64748b', marginTop: 3 }}>
          Calibrated probabilities; reasons are sampling Shapley values. Model {m.version}. Trained on rule-derived weak labels - see docs/EVALUATION.md.
        </div>
      )}
    </Row>
  );
}

function BaselineRow({ p }: { p: ThermalProperties }) {
  const b = baselineSummary(p);
  const st = DEVIATION_STYLE[b.deviation];
  const monthNow = p.last_seen ? new Date(p.last_seen).getUTCMonth() : -1;
  return (
    <Row k="Compared with its own history (365 days)">
      <div data-testid="baseline-headline" style={{ fontWeight: 600 }}>{b.headline}</div>
      <div style={{ fontSize: 12, color: '#475569', fontWeight: 400 }}>{b.detail}</div>
      {b.today && (
        <div data-testid="baseline-today" style={{ marginTop: 4, fontSize: 12, padding: '3px 8px', borderRadius: 8,
          background: st.bg, color: st.fg, fontWeight: 600, display: 'inline-block' }}>
          Today {b.today} - {st.label}
        </div>
      )}
      {b.monthly.length === 12 && (
        <div style={{ marginTop: 6 }} aria-label="Share of days active by month">
          <div style={{ display: 'flex', gap: 2, alignItems: 'flex-end', height: 28 }}>
            {b.monthly.map((v, i) => (
              <div key={i} title={`${MONTH_LABELS[i]}: ${v == null ? 'no data' : `${Math.round(v * 100)}% of days`}`}
                style={{ flex: 1, height: `${Math.max(((v ?? 0) * 100) * 0.28, v ? 2 : 0)}px`, minHeight: v ? 2 : 0,
                  background: i === monthNow ? '#0369a1' : '#94a3b8', borderRadius: 2, opacity: v == null ? 0.25 : 1 }} />
            ))}
          </div>
          <div style={{ display: 'flex', gap: 2, fontSize: 8, color: '#64748b' }}>
            {MONTH_LABELS.map(m => <span key={m} style={{ flex: 1, textAlign: 'center' }}>{m[0]}</span>)}
          </div>
        </div>
      )}
      {b.available && (
        <div style={{ fontSize: 12, color: '#64748b', marginTop: 3 }}>
          Baseline excludes the most recent 14 days; low-confidence pixels are dropped.
        </div>
      )}
    </Row>
  );
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
            <p style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.14em', color: '#fcd34d', margin: '0 0 4px' }}>Thermal source · NASA FIRMS</p>
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
            <div style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>
              Evidence margin {Math.round(p.class_margin * 100)} points → <strong>{p.class_confidence}</strong> separation from the next class.
              A heuristic score, not a verified cause.
            </div>
          </Row>

          <Row k="Class scores (share of evidence)">
            {Object.entries(p.class_scores).sort(([, a], [, b]) => b - a).slice(0, 4).map(([id, v]) => (
              <div key={id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, margin: '2px 0' }}>
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
            <div style={{ fontSize: 12, color: '#64748b' }}>Dark bars = night passes, amber = day. Height = FRP.</div>
          </Row>

          <ModelRow p={p} />

          <BaselineRow p={p} />

          <Row k="First / last detection">
            {new Date(p.first_seen).toLocaleString()} → {new Date(p.last_seen).toLocaleString()}
          </Row>

          <Row k="Nearest mapped facility">
            {p.site_name || p.site_ref ? (
              <>
                {p.site_name ?? p.site_ref} · {(p.site_kind ?? '').replace(/_/g, ' ')} ·{' '}
                {p.site_distance_m != null ? `${Math.round(p.site_distance_m)} m away` : NA}
                {p.site_within ? ' (inside its matching radius)' : ' (outside its matching radius - not treated as the source)'}
                {/* OSM refs (way/123) link to OpenStreetMap; merged facility ids are shown on the Facilities layer instead */}
                {p.site_ref && /^(node|way|relation)\/\d+$/.test(p.site_ref)
                  ? <> · <a href={`https://www.openstreetmap.org/${p.site_ref}`} target="_blank" rel="noopener noreferrer" style={{ color: '#0369a1' }}>{p.site_ref}</a></>
                  : p.site_ref && <> · merged facility database (sources on the Facilities layer)</>}
              </>
            ) : 'None within range'}
          </Row>

          <Row k="Land cover around the source (ESA WorldCover 2021)">
            {p.lc_dominant ? <>{p.lc_dominant} dominant · crop {Math.round((p.lc_crop ?? 0) * 100)}% · natural vegetation {Math.round((p.lc_vegetation ?? 0) * 100)}% · built {Math.round((p.lc_built ?? 0) * 100)}% · bare {Math.round((p.lc_bare ?? 0) * 100)}%</> : NA}
          </Row>

          <Row k="Satellite imagery (Sentinel-2 after / before, dNBR, SWIR hot pixels; Landsat thermal)">
            <ImageryPanel sourceId={p.source_id} />
          </Row>

          <Row k="Location">{lat.toFixed(5)}, {lon.toFixed(5)} · instruments: {p.instruments.replace(/VIIRS_|_NRT/g, '')}</Row>
          <Row k="Source ID"><code style={{ fontSize: 12 }}>{p.source_id}</code></Row>
          <p style={{ fontSize: 12.5, color: '#64748b', marginTop: 10, lineHeight: 1.45 }}>
            Thermal anomalies are detected by NASA VIIRS at 375 m. A detection shows heat, not its cause: confirm industrial incidents with
            high-resolution imagery or ground reports. Data: NASA FIRMS, © OpenStreetMap contributors (ODbL), ESA WorldCover (CC BY 4.0).
          </p>
        </div>
      )}
    </aside>
  );
}
