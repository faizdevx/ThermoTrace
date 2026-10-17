import { useEffect, useRef, useState } from 'react';
import type { LiveConnection } from '@/hooks/useLiveUpdates';
import type { SyncSource, SyncState, SyncStatusResponse } from '@/types/industrial';

export interface UiUpdateInfo {
  revision: number;
  /** Seconds between the server-side commit and the moment the refreshed data was rendered. */
  commitToUiSeconds: number | null;
  atMs: number;
}

const STATE_STYLE: Record<SyncState | 'unreachable' | 'connecting', { label: string; color: string; bg: string }> = {
  live: { label: 'Live', color: '#15803d', bg: 'rgba(34,197,94,0.12)' },
  syncing: { label: 'Syncing', color: '#0369a1', bg: 'rgba(14,165,233,0.14)' },
  delayed: { label: 'Delayed', color: '#b45309', bg: 'rgba(245,158,11,0.16)' },
  offline: { label: 'Offline', color: '#b91c1c', bg: 'rgba(239,68,68,0.14)' },
  not_configured: { label: 'Not configured', color: '#475569', bg: 'rgba(100,116,139,0.14)' },
  awaiting_setup: { label: 'Awaiting import', color: '#475569', bg: 'rgba(100,116,139,0.14)' },
  unreachable: { label: 'Offline', color: '#b91c1c', bg: 'rgba(239,68,68,0.14)' },
  connecting: { label: 'Connecting…', color: '#475569', bg: 'rgba(100,116,139,0.14)' },
};

export function formatAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return 'n/a';
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
  return `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h`;
}

function ageFromIso(iso: string | null, nowMs: number): string {
  if (!iso) return 'never';
  const t = Date.parse(iso);
  return Number.isNaN(t) ? 'n/a' : `${formatAge((nowMs - t) / 1000)} ago`;
}

const SOURCE_LABEL: Record<string, string> = {
  openstreetmap: 'OpenStreetMap',
  nasa_firms: 'NASA FIRMS thermal',
  gov_india_industrial_land_bank: 'India Industrial Land Bank',
  gov_industrial_corridor_land: 'Industrial Corridor Land Platform',
  gov_ncog_up_industrial_parks: 'NCoG / UP Industrial Parks GIS',
};

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 12, padding: '1px 0' }}>
      <span style={{ color: '#64748b' }}>{k}</span>
      <span style={{ color: '#0f172a', fontWeight: 600, textAlign: 'right' }}>{v}</span>
    </div>
  );
}

function SourceBlock({ s, nowMs }: { s: SyncSource; nowMs: number }) {
  const st = STATE_STYLE[s.state];
  return (
    <div style={{ borderTop: '1px solid #e2e8f0', paddingTop: 8, marginTop: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <strong style={{ fontSize: 12, color: '#0f172a' }}>{SOURCE_LABEL[s.source] ?? s.source}</strong>
        <span style={{ fontSize: 12, fontWeight: 700, padding: '1px 8px', borderRadius: 99, color: st.color, background: st.bg }}>
          {st.label}
        </span>
      </div>
      <div style={{ fontSize: 12, color: '#475569', marginBottom: 4 }}>{s.reason}</div>
      {s.enabled && (
        <>
          <Row k="Source cadence" v={s.update_cadence ?? 'unknown'} />
          <Row k="Last successful check" v={ageFromIso(s.last_check_at, nowMs)} />
          <Row k="Last data change in source" v={s.last_source_data_at ? `${ageFromIso(s.last_source_data_at, nowMs)}` : 'n/a'} />
          <Row k="Last database commit" v={ageFromIso(s.last_commit_at, nowMs)} />
          <Row k="Behind source head" v={s.replication_steps_behind ? `${s.replication_steps_behind} step(s), ${formatAge(s.lag_behind_source_seconds)}` : 'caught up'} />
          <Row k="Freshness target" v={s.freshness_target_seconds ? formatAge(s.freshness_target_seconds) : 'n/a'} />
        </>
      )}
      {s.last_error && s.state !== 'not_configured' && (
        <div style={{ marginTop: 4, fontSize: 12, color: '#b91c1c', wordBreak: 'break-word' }}>
          Unresolved error: {s.last_error}
          {s.next_retry_at ? ` (retry ${ageFromIso(s.next_retry_at, nowMs).replace(' ago', '')} from now)` : ''}
        </div>
      )}
    </div>
  );
}

interface Props {
  status: SyncStatusResponse | null;
  unreachable: boolean;
  connection: LiveConnection;
  lastUiUpdate: UiUpdateInfo | null;
  onRefresh: () => void;
}

export function LiveStatus({ status, unreachable, connection, lastUiUpdate, onRefresh }: Props) {
  const [open, setOpen] = useState(false);
  const [nowMs, setNowMs] = useState(Date.now());
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 5000);
    return () => clearInterval(id);
  }, []);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!rootRef.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const dbDown = status?.database.status === 'unavailable';
  const overallKey: SyncState | 'unreachable' | 'connecting' =
    unreachable ? 'unreachable' : !status ? 'connecting' : dbDown ? 'offline' : status.overall.state;
  const style = STATE_STYLE[overallKey];
  const synthetic = status?.data_mode === 'synthetic';
  const osm = status?.sources.find(s => s.source === 'openstreetmap');

  return (
    <div ref={rootRef} style={{ position: 'relative' }}>
      <button
        id="live-status-button"
        onClick={() => { setOpen(o => !o); onRefresh(); }}
        aria-expanded={open}
        aria-label={`Synchronization status: ${style.label}`}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer',
          padding: '3px 10px', borderRadius: 99, border: `1px solid ${style.color}33`,
          background: style.bg, color: style.color, fontSize: 12, fontWeight: 700,
          fontFamily: "'IBM Plex Sans', sans-serif",
        }}
      >
        <span style={{
          width: 8, height: 8, borderRadius: '50%', background: style.color, display: 'inline-block',
          animation: overallKey === 'syncing' ? 'pulse 1s ease-in-out infinite' : undefined,
        }} />
        {synthetic ? 'Demo (no sync)' : style.label}
        {!synthetic && osm?.data_age_seconds != null && overallKey !== 'unreachable' && overallKey !== 'connecting' && (
          <span style={{ fontWeight: 500, opacity: 0.85 }}>· data {formatAge(osm.data_age_seconds)} old</span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Synchronization details"
          style={{
            position: 'absolute', right: 0, bottom: 'calc(100% + 8px)', zIndex: 1200, width: 340, maxWidth: '90vw',
            background: '#fff', borderRadius: 14, border: '1px solid #e2e8f0',
            boxShadow: '0 12px 40px rgba(15,23,42,0.2)', padding: 14, fontFamily: "'IBM Plex Sans', sans-serif",
          }}
        >
          <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.1em', color: '#64748b', textTransform: 'uppercase', marginBottom: 6 }}>
            Status (reported separately)
          </div>
          <Row k="API" v={unreachable ? 'unreachable' : 'alive'} />
          <Row k="Database" v={status ? status.database.status : 'unknown'} />
          <Row k="Change notifications" v={connection === 'sse' ? 'live stream (SSE)' : connection === 'polling' ? 'polling fallback (10 s)' : connection === 'connecting' ? 'connecting…' : 'unavailable'} />
          <Row k="Data revision" v={status?.revision != null ? String(status.revision) : 'n/a'} />
          {lastUiUpdate && (
            <Row
              k="Last live update on screen"
              v={`rev ${lastUiUpdate.revision}${lastUiUpdate.commitToUiSeconds != null ? `, ${formatAge(lastUiUpdate.commitToUiSeconds)} after commit` : ''}`}
            />
          )}
          {status?.sources.map(s => <SourceBlock key={s.source} s={s} nowMs={nowMs} />)}
          <div style={{ marginTop: 10, fontSize: 12, color: '#64748b', lineHeight: 1.4 }}>
            "Live" means checked recently, caught up with the source, and within its freshness target.
            It does not mean real-time: OpenStreetMap regional diffs are published about once a day.
          </div>
        </div>
      )}
    </div>
  );
}
