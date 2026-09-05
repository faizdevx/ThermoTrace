import { type ReactNode } from 'react';
import type { IndustrialSiteFeature } from '@/types/industrial';


interface SiteDetailPanelProps {
  site: IndustrialSiteFeature | null;
  onClose: () => void;
}

const CONFIDENCE_STYLES: Record<string, { bg: string; text: string; border: string; label: string }> = {
  high: { bg: '#f0fdf4', text: '#16a34a', border: '#86efac', label: 'High Confidence' },
  medium: { bg: '#fffbeb', text: '#d97706', border: '#fcd34d', label: 'Medium Confidence' },
  low: { bg: '#fef2f2', text: '#dc2626', border: '#fca5a5', label: 'Low Confidence' },
};

const STATUS_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  operational: { bg: '#f0fdf4', text: '#16a34a', label: 'Operational' },
  under_construction: { bg: '#fffbeb', text: '#d97706', label: 'Under Construction' },
  temporarily_closed: { bg: '#fef2f2', text: '#dc2626', label: 'Temporarily Closed' },
  closed: { bg: '#f8fafc', text: '#64748b', label: 'Closed' },
};

function ScoreBar({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color = pct >= 90 ? '#22c55e' : pct >= 75 ? '#f59e0b' : '#ef4444';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div style={{ flex: 1, height: 6, borderRadius: 99, background: '#e2e8f0', overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', borderRadius: 99, background: color, transition: 'width 0.5s ease' }} />
      </div>
      <span style={{ fontSize: 12, fontWeight: 700, color, minWidth: 32 }}>{pct}%</span>
    </div>
  );
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, padding: '10px 0', borderBottom: '1px solid #f1f5f9' }}>
      <span style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#94a3b8' }}>{label}</span>
      <span style={{ fontSize: 13, color: '#1e293b', fontWeight: 500 }}>{value}</span>
    </div>
  );
}

function Badge({ text, bg, color, border }: { text: string; bg: string; color: string; border?: string }) {
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      padding: '3px 10px',
      borderRadius: 99,
      fontSize: 11,
      fontWeight: 600,
      background: bg,
      color,
      border: border ? `1px solid ${border}` : undefined,
      textTransform: 'uppercase',
      letterSpacing: '0.06em',
    }}>
      {text}
    </span>
  );
}

export function SiteDetailPanel({ site, onClose }: SiteDetailPanelProps) {
  const isOpen = site !== null;

  return (
    <>
      {/* Backdrop overlay on mobile */}
      {isOpen && (
        <div
          onClick={onClose}
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.15)',
            zIndex: 399,
            display: 'none',
          }}
          className="site-panel-backdrop"
        />
      )}

      <aside
        style={{
          position: 'fixed',
          top: 0,
          right: 0,
          height: '100vh',
          width: 360,
          maxWidth: '100vw',
          background: '#fff',
          boxShadow: '-4px 0 40px rgba(15, 23, 42, 0.12)',
          zIndex: 400,
          transform: isOpen ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
          display: 'flex',
          flexDirection: 'column',
          overflowY: 'auto',
          fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif",
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          padding: '20px 20px 16px',
          borderBottom: '1px solid #f1f5f9',
          background: 'linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%)',
          color: '#fff',
          position: 'sticky',
          top: 0,
          zIndex: 1,
        }}>
          <div style={{ flex: 1, paddingRight: 12 }}>
            <p style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.12em', color: '#7dd3fc', margin: 0, marginBottom: 6 }}>
              Industrial Site
            </p>
            <h2 style={{ fontSize: 16, fontWeight: 700, color: '#f8fafc', margin: 0, lineHeight: 1.3 }}>
              {site?.properties.name ?? '—'}
            </h2>
            {site && (
              <p style={{ fontSize: 12, color: '#94a3b8', margin: '6px 0 0' }}>
                {site.properties.district}, {site.properties.state}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close detail panel"
            style={{
              width: 32, height: 32, borderRadius: '50%', border: 'none',
              background: 'rgba(255,255,255,0.12)', color: '#fff',
              fontSize: 18, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0, transition: 'background 0.2s',
            }}
          >
            ×
          </button>
        </div>

        {site && (
          <div style={{ padding: '0 20px 24px', flex: 1 }}>

            {/* Status & confidence badges */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', padding: '16px 0', borderBottom: '1px solid #f1f5f9' }}>
              {(() => {
                const ss = STATUS_STYLES[site.properties.status] ?? STATUS_STYLES.closed;
                return <Badge text={ss.label} bg={ss.bg} color={ss.text} />;
              })()}
              {(() => {
                const cs = CONFIDENCE_STYLES[site.properties.match_confidence] ?? CONFIDENCE_STYLES.low;
                return <Badge text={cs.label} bg={cs.bg} color={cs.text} border={cs.border} />;
              })()}
            </div>

            {/* Match score */}
            <div style={{ padding: '14px 0', borderBottom: '1px solid #f1f5f9' }}>
              <span style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#94a3b8', display: 'block', marginBottom: 8 }}>Match Score</span>
              <ScoreBar score={site.properties.match_score} />
              <p style={{ fontSize: 11, color: '#94a3b8', margin: '6px 0 0' }}>Method: {site.properties.match_method}</p>
            </div>

            {/* Core details */}
            <Row label="Site ID" value={<code style={{ fontFamily: 'monospace', fontSize: 12, background: '#f8fafc', padding: '1px 6px', borderRadius: 4 }}>{site.properties.site_id}</code>} />
            <Row label="Industry Type" value={site.properties.industry_type.replace(/_/g, ' ')} />
            <Row label="Address" value={site.properties.address} />
            <Row label="Coordinates" value={`${site.geometry.coordinates[1].toFixed(4)}°N, ${site.geometry.coordinates[0].toFixed(4)}°E`} />
            <Row label="Last Verified" value={site.properties.last_verified} />

            {/* Provenance */}
            <div style={{ marginTop: 4 }}>
              <span style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#94a3b8', display: 'block', padding: '10px 0 8px', borderBottom: '1px solid #f1f5f9' }}>Data Provenance</span>

              <div style={{ padding: '10px 0', borderBottom: '1px solid #f1f5f9' }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: '#475569', display: 'block', marginBottom: 6 }}>Data Sources</span>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {site.properties.data_sources.map(src => (
                    <Badge key={src} text={src} bg="#f1f5f9" color="#475569" />
                  ))}
                </div>
              </div>

              {site.properties.osm_ids.length > 0 && (
                <div style={{ padding: '10px 0', borderBottom: '1px solid #f1f5f9' }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: '#475569', display: 'block', marginBottom: 4 }}>OSM IDs</span>
                  {site.properties.osm_ids.map(id => (
                    <a
                      key={id}
                      href={`https://www.openstreetmap.org/node/${id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ fontSize: 12, color: '#0ea5e9', textDecoration: 'none', display: 'block' }}
                    >
                      osm:{id} ↗
                    </a>
                  ))}
                </div>
              )}

              {site.properties.government_ids.length > 0 && (
                <div style={{ padding: '10px 0', borderBottom: '1px solid #f1f5f9' }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: '#475569', display: 'block', marginBottom: 4 }}>Government IDs</span>
                  {site.properties.government_ids.map(id => (
                    <span key={id} style={{ fontSize: 12, color: '#64748b', display: 'block', fontFamily: 'monospace' }}>{id}</span>
                  ))}
                </div>
              )}

              <div style={{ padding: '10px 0' }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: '#475569', display: 'block', marginBottom: 4 }}>Source Count</span>
                <span style={{ fontSize: 13, color: '#1e293b' }}>{site.properties.source_count} data source{site.properties.source_count !== 1 ? 's' : ''}</span>
              </div>

              {site.properties.review_required && (
                <div style={{
                  marginTop: 4, padding: '10px 14px', background: '#fffbeb', borderRadius: 10,
                  border: '1px solid #fcd34d', display: 'flex', gap: 8, alignItems: 'flex-start',
                }}>
                  <span style={{ fontSize: 16 }}>⚠️</span>
                  <p style={{ fontSize: 11, color: '#92400e', margin: 0, lineHeight: 1.5 }}>
                    This record is flagged for manual review. Match confidence may be insufficient for automated acceptance.
                  </p>
                </div>
              )}
            </div>
          </div>
        )}
      </aside>
    </>
  );
}
