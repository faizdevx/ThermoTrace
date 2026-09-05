import { type ReactNode } from 'react';
import type { IndustrialSiteFeature } from '@/types/industrial';


// ── Helpers ───────────────────────────────────────────────────────────────────

function safe(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined || value === '') return fallback;
  if (typeof value === 'number' && isNaN(value)) return fallback;
  return String(value);
}

function formatLabel(raw: string): string {
  return raw.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// ── Style maps ────────────────────────────────────────────────────────────────

const CONFIDENCE_MAP: Record<string, { bg: string; fg: string; border: string; dot: string }> = {
  high: { bg: '#f0fdf4', fg: '#15803d', border: '#86efac', dot: '#22c55e' },
  medium: { bg: '#fffbeb', fg: '#b45309', border: '#fcd34d', dot: '#f59e0b' },
  low: { bg: '#fef2f2', fg: '#b91c1c', border: '#fca5a5', dot: '#ef4444' },
};

const STATUS_MAP: Record<string, { bg: string; fg: string; label: string }> = {
  operational: { bg: '#f0fdf4', fg: '#15803d', label: 'Operational' },
  under_construction: { bg: '#fffbeb', fg: '#b45309', label: 'Under Construction' },
  temporarily_closed: { bg: '#fff1f2', fg: '#be123c', label: 'Temporarily Closed' },
  closed: { bg: '#f8fafc', fg: '#64748b', label: 'Closed' },
};

const INDUSTRY_ICONS: Record<string, string> = {
  logistics: '🚢', automotive: '🚗', engineering: '⚙️', textiles: '🧵',
  manufacturing: '🏭', petrochemicals: '🛢️', electronics: '💡', energy: '⚡',
  heavy_engineering: '🔩', foundry: '🔥', pharmaceuticals: '💊', fabrication: '🔧',
  mixed_manufacturing: '🏗️', chemicals: '⚗️', steel: '🏗️', food_processing: '🌾',
  marine: '⚓', metalworks: '🔨', renewables: '🌱',
};

// ── Sub-components ────────────────────────────────────────────────────────────

function Chip({ text, bg, fg, border }: { text: string; bg: string; fg: string; border?: string }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 10px', borderRadius: 99,
      fontSize: 11, fontWeight: 600,
      background: bg, color: fg,
      border: border ? `1px solid ${border}` : undefined,
      textTransform: 'uppercase', letterSpacing: '0.05em',
      fontFamily: "'IBM Plex Sans', sans-serif",
    }}>
      {text}
    </span>
  );
}

function DataRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ padding: '9px 0', borderBottom: '1px solid #f1f5f9' }}>
      <p style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#94a3b8', margin: '0 0 3px' }}>
        {label}
      </p>
      <div style={{ fontSize: 13, color: '#1e293b', fontWeight: 500, lineHeight: 1.5 }}>
        {children}
      </div>
    </div>
  );
}

function ScoreBar({ score }: { score: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
  const color = pct >= 90 ? '#22c55e' : pct >= 75 ? '#f59e0b' : '#ef4444';
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ flex: 1, height: 7, borderRadius: 99, background: '#e2e8f0', overflow: 'hidden' }}>
          <div style={{
            height: '100%', borderRadius: 99,
            background: `linear-gradient(90deg, ${color}cc, ${color})`,
            width: `${pct}%`,
            transition: 'width 0.6s cubic-bezier(0.34,1.56,0.64,1)',
          }} />
        </div>
        <span style={{ fontSize: 13, fontWeight: 700, color, minWidth: 34, textAlign: 'right' }}>{pct}%</span>
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <p style={{
      fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
      letterSpacing: '0.1em', color: '#64748b',
      margin: '16px 0 0', padding: '8px 0 6px',
      borderTop: '1px solid #f1f5f9',
    }}>
      {children}
    </p>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface IndustryDetailsProps {
  site: IndustrialSiteFeature | null;
  onClose: () => void;
}

export function IndustryDetails({ site, onClose }: IndustryDetailsProps) {
  const isOpen = site !== null;
  const p = site?.properties;
  const geo = site?.geometry;

  const statusStyle = STATUS_MAP[p?.status ?? ''] ?? STATUS_MAP.closed;
  const confidenceStyle = CONFIDENCE_MAP[p?.match_confidence ?? ''] ?? CONFIDENCE_MAP.low;
  const industryIcon = INDUSTRY_ICONS[p?.industry_type ?? ''] ?? '🏭';

  return (
    <aside
      aria-label="Industrial site details"
      style={{
        position: 'fixed', top: 0, right: 0,
        width: 380, maxWidth: '100vw', height: '100vh',
        background: '#fff',
        boxShadow: '-6px 0 48px rgba(15,23,42,0.14)',
        zIndex: 600,
        transform: isOpen ? 'translateX(0)' : 'translateX(100%)',
        transition: 'transform 0.32s cubic-bezier(0.4,0,0.2,1)',
        display: 'flex', flexDirection: 'column',
        fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif",
        overflowY: 'auto',
      }}
    >
      {/* ── Dark gradient header ──── */}
      <div style={{
        background: 'linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%)',
        padding: '18px 20px 16px',
        flexShrink: 0,
        position: 'sticky', top: 0, zIndex: 1,
      }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          {/* Industry icon bubble */}
          <div style={{
            width: 40, height: 40, borderRadius: 12, flexShrink: 0, marginTop: 2,
            background: 'rgba(255,255,255,0.12)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 20,
          }}>
            {isOpen ? industryIcon : '🏭'}
          </div>

          <div style={{ flex: 1, overflow: 'hidden' }}>
            <p style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.14em', color: '#7dd3fc', margin: '0 0 4px' }}>
              Industrial Site
            </p>
            <h2 style={{
              fontSize: 15, fontWeight: 700, color: '#f8fafc',
              margin: 0, lineHeight: 1.35,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {safe(p?.name)}
            </h2>
            {p && (
              <p style={{ fontSize: 12, color: '#94a3b8', margin: '4px 0 0' }}>
                {safe(p.district)} · {safe(p.state)}
              </p>
            )}
          </div>

          <button
            onClick={onClose}
            aria-label="Close panel"
            style={{
              width: 32, height: 32, borderRadius: '50%',
              border: 'none', background: 'rgba(255,255,255,0.1)',
              color: '#fff', fontSize: 18, lineHeight: 1,
              cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0, transition: 'background 0.2s',
            }}
            onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.2)')}
            onMouseLeave={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.1)')}
          >
            ×
          </button>
        </div>
      </div>

      {/* ── Body ──── */}
      {p && geo && (
        <div style={{ padding: '12px 20px 28px', flex: 1 }}>

          {/* Status + confidence badges */}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '10px 0 12px', borderBottom: '1px solid #f1f5f9' }}>
            <Chip text={statusStyle.label} bg={statusStyle.bg} fg={statusStyle.fg} />
            <Chip
              text={`${formatLabel(p.match_confidence)} Confidence`}
              bg={confidenceStyle.bg} fg={confidenceStyle.fg} border={confidenceStyle.border}
            />
          </div>

          {/* Match score */}
          <div style={{ padding: '12px 0 0' }}>
            <p style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#94a3b8', margin: '0 0 8px' }}>
              Match Score
            </p>
            <ScoreBar score={p.match_score} />
            <p style={{ fontSize: 11, color: '#94a3b8', margin: '5px 0 0' }}>
              Method: <span style={{ color: '#64748b', fontWeight: 500 }}>{safe(p.match_method)}</span>
            </p>
          </div>

          {/* Core details */}
          <SectionLabel>Location &amp; Classification</SectionLabel>

          <DataRow label="Site ID">
            <code style={{ fontFamily: 'monospace', fontSize: 12, background: '#f8fafc', padding: '1px 6px', borderRadius: 4, color: '#0ea5e9' }}>
              {safe(p.site_id)}
            </code>
          </DataRow>

          <DataRow label="Industry Type">
            {industryIcon}&nbsp;{formatLabel(safe(p.industry_type))}
          </DataRow>

          <DataRow label="Address">
            {safe(p.address)}
          </DataRow>

          <DataRow label="State · District">
            {safe(p.state)} · {safe(p.district)}
          </DataRow>

          <DataRow label="Coordinates">
            <span style={{ fontFamily: 'monospace', fontSize: 12 }}>
              {geo.coordinates[1].toFixed(5)}° N,&nbsp;
              {geo.coordinates[0].toFixed(5)}° E
            </span>
          </DataRow>

          <DataRow label="Last Verified">
            {safe(p.last_verified)}
          </DataRow>

          {/* Provenance */}
          <SectionLabel>Data Provenance</SectionLabel>

          <DataRow label="Data Sources">
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 2 }}>
              {p.data_sources.length > 0
                ? p.data_sources.map(src => (
                    <Chip key={src} text={src} bg="#f1f5f9" fg="#475569" />
                  ))
                : <span style={{ color: '#94a3b8' }}>No sources recorded</span>
              }
            </div>
          </DataRow>

          <DataRow label="Source Count">
            {p.source_count} data source{p.source_count !== 1 ? 's' : ''}
          </DataRow>

          {p.osm_ids.length > 0 && (
            <DataRow label="OSM IDs">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {p.osm_ids.map(id => (
                  <a
                    key={id}
                    href={`https://www.openstreetmap.org/way/${id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ fontSize: 12, color: '#0ea5e9', textDecoration: 'none', fontFamily: 'monospace' }}
                  >
                    osm:{id} ↗
                  </a>
                ))}
              </div>
            </DataRow>
          )}

          {p.government_ids.length > 0 && (
            <DataRow label="Government IDs">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {p.government_ids.map(id => (
                  <span key={id} style={{ fontSize: 12, fontFamily: 'monospace', color: '#475569' }}>
                    {id}
                  </span>
                ))}
              </div>
            </DataRow>
          )}

          {/* Review warning */}
          {p.review_required && (
            <div style={{
              marginTop: 14, padding: '10px 14px',
              background: '#fffbeb', borderRadius: 12,
              border: '1px solid #fcd34d',
              display: 'flex', gap: 10, alignItems: 'flex-start',
            }}>
              <span style={{ fontSize: 18, lineHeight: 1 }}>⚠️</span>
              <p style={{ fontSize: 12, color: '#92400e', margin: 0, lineHeight: 1.5 }}>
                Flagged for manual review. Match confidence may be insufficient for automated acceptance.
              </p>
            </div>
          )}
        </div>
      )}

      {/* Empty state when no site selected */}
      {!p && (
        <div style={{
          flex: 1, display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center',
          padding: 32, textAlign: 'center', color: '#94a3b8',
        }}>
          <span style={{ fontSize: 40, marginBottom: 12 }}>🗺️</span>
          <p style={{ fontSize: 14, fontWeight: 600, color: '#64748b', margin: '0 0 6px' }}>No site selected</p>
          <p style={{ fontSize: 13, margin: 0, lineHeight: 1.5 }}>Click a marker on the map to view full site details.</p>
        </div>
      )}
    </aside>
  );
}
