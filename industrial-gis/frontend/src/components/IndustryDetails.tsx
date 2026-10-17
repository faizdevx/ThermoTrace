import { type ReactNode } from 'react';
import type { IndustrialSiteFeature } from '@/types/industrial';

const NA = 'Not available in source';

function show(value: unknown, fallback = NA): string {
  if (value === null || value === undefined || value === '') return fallback;
  if (typeof value === 'number' && Number.isNaN(value)) return fallback;
  return String(value);
}

function label(raw: string): string {
  return raw.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

const EVIDENCE: Record<string, { bg: string; fg: string; border: string }> = {
  high: { bg: '#f0fdf4', fg: '#15803d', border: '#86efac' },
  medium: { bg: '#fffbeb', fg: '#b45309', border: '#fcd34d' },
  low: { bg: '#fef2f2', fg: '#b91c1c', border: '#fca5a5' },
};

const FEATURE_CLASS_HELP: Record<string, string> = {
  landuse_area: 'Industrial land-use area (a zone, not a confirmed single factory)',
  facility: 'Tagged industrial facility / works',
  building: 'Industrial building',
  power_plant: 'Power plant',
  utility: 'Water / wastewater utility plant',
};

function Chip({ text, bg, fg, border }: { text: string; bg: string; fg: string; border?: string }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', padding: '3px 10px', borderRadius: 99,
      fontSize: 12, fontWeight: 600, background: bg, color: fg,
      border: border ? `1px solid ${border}` : undefined, letterSpacing: '0.04em',
    }}>
      {text}
    </span>
  );
}

function DataRow({ label: title, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ padding: '9px 0', borderBottom: '1px solid #f1f5f9' }}>
      <p style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#64748b', margin: '0 0 3px' }}>
        {title}
      </p>
      <div style={{ fontSize: 13, color: '#1e293b', fontWeight: 500, lineHeight: 1.5, wordBreak: 'break-word' }}>
        {children}
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <p style={{
      fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#64748b',
      margin: '16px 0 0', padding: '8px 0 6px', borderTop: '1px solid #f1f5f9',
    }}>
      {children}
    </p>
  );
}

interface Props {
  site: IndustrialSiteFeature | null;
  /** Set when the open site vanished from the dataset (deleted/retagged in the source). */
  removed: boolean;
  onClose: () => void;
}

export function IndustryDetails({ site, removed, onClose }: Props) {
  const isOpen = site !== null;
  const p = site?.properties;
  const evidence = EVIDENCE[p?.evidence_level ?? 'low'] ?? EVIDENCE.low;
  const tags = p?.tags ?? {};
  const tagEntries = Object.entries(tags).sort(([a], [b]) => a.localeCompare(b));

  return (
    <aside
      aria-label="Industrial site details"
      aria-hidden={!isOpen}
      style={{
        position: 'fixed', top: 0, right: 0, width: 380, maxWidth: '100vw', height: '100vh',
        background: '#fff', boxShadow: '-6px 0 48px rgba(15,23,42,0.14)', zIndex: 600,
        transform: isOpen ? 'translateX(0)' : 'translateX(100%)',
        transition: 'transform 0.32s cubic-bezier(0.4,0,0.2,1)',
        display: 'flex', flexDirection: 'column',
        fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif", overflowY: 'auto',
      }}
    >
      <div style={{
        background: 'linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%)', padding: '18px 20px 16px',
        flexShrink: 0, position: 'sticky', top: 0, zIndex: 1,
      }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <div style={{ flex: 1, overflow: 'hidden' }}>
            <p style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.14em', color: '#7dd3fc', margin: '0 0 4px' }}>
              {p ? (FEATURE_CLASS_HELP[p.feature_class ?? '']?.split(' (')[0] ?? 'Industrial site') : 'Industrial site'}
            </p>
            <h2 style={{ fontSize: 15, fontWeight: 700, color: '#f8fafc', margin: 0, lineHeight: 1.35, wordBreak: 'break-word' }}>
              {p ? (p.name || `Unnamed ${p.industry_type || 'site'}`) : ''}
            </h2>
            {p && (
              <p style={{ fontSize: 12, color: '#64748b', margin: '4px 0 0' }}>
                {[p.district, p.state].filter(Boolean).join(' · ') || 'Administrative area not assigned'}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close panel"
            style={{
              width: 32, height: 32, borderRadius: '50%', border: 'none', background: 'rgba(255,255,255,0.1)',
              color: '#fff', fontSize: 18, lineHeight: 1, cursor: 'pointer', flexShrink: 0,
            }}
          >
            ×
          </button>
        </div>
      </div>

      {p && site && (
        <div style={{ padding: '12px 20px 28px', flex: 1 }}>
          {removed && (
            <div role="alert" style={{ margin: '4px 0 10px', padding: '8px 10px', borderRadius: 10, background: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b', fontSize: 12 }}>
              This site is no longer in the dataset: the OpenStreetMap object was deleted or no longer
              carries an industrial tag. The values below are the last known ones.
            </div>
          )}

          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '10px 0 12px', borderBottom: '1px solid #f1f5f9' }}>
            <Chip text={label(p.industry_type) || 'Unclassified'} bg="#f1f5f9" fg="#475569" />
            <Chip text={`Mapped status: ${label(p.status) || 'unknown'}`} bg="#f8fafc" fg="#64748b" />
            <Chip text={`Evidence: ${p.evidence_level ?? 'n/a'}`} bg={evidence.bg} fg={evidence.fg} border={evidence.border} />
          </div>

          <DataRow label="Classification">
            {FEATURE_CLASS_HELP[p.feature_class ?? ''] ?? show(p.feature_class)}
          </DataRow>
          <DataRow label="Evidence level (calculated)">
            {show(p.evidence_level)} (score {show(p.evidence_score, '0')}/4)
            <div style={{ fontSize: 12, color: '#64748b', fontWeight: 400, marginTop: 2 }}>
              Counts how many of: a name, an operator/owner, a specific industrial type, and an identifier/website
              are tagged. This is tag completeness, <strong>not</strong> official verification or proof the site is operating.
            </div>
          </DataRow>
          <DataRow label="Address (from tags)">{show(p.address)}</DataRow>

          <SectionLabel>Location</SectionLabel>
          <DataRow label="Coordinates (representative point)">
            {p.latitude != null && p.longitude != null ? `${p.latitude.toFixed(5)}, ${p.longitude.toFixed(5)}` : NA}
          </DataRow>
          <DataRow label="Geometry">
            {show(p.geometry_type ? label(p.geometry_type.toLowerCase()) : '')}
            {p.area_m2 != null && ` · ${(p.area_m2 / 10000).toLocaleString(undefined, { maximumFractionDigits: 2 })} ha`}
          </DataRow>
          <DataRow label="State / District (derived by spatial join with geoBoundaries)">
            {show(p.state, 'Unassigned')} / {show(p.district, 'Unassigned')}
          </DataRow>

          <SectionLabel>Provenance</SectionLabel>
          <DataRow label="Source">
            {p.primary_source === 'openstreetmap' ? 'OpenStreetMap (ODbL 1.0)' : show(p.primary_source)}
            {p.source_url && (
              <> · <a href={p.source_url} target="_blank" rel="noopener noreferrer" style={{ color: '#0369a1' }}>{p.source_ref || 'view object'}</a></>
            )}
          </DataRow>
          <DataRow label="Source object version / last edit">
            {p.osm_version != null ? `v${p.osm_version}` : NA}
            {p.source_updated_at && ` · ${new Date(p.source_updated_at).toLocaleString()}`}
          </DataRow>
          <DataRow label="Government verification">
            None. No government source is integrated; this record comes from OpenStreetMap only.
          </DataRow>
          <DataRow label="Site ID (stable)">
            <code style={{ fontSize: 12 }}>{p.site_id}</code>
          </DataRow>
          <DataRow label="Last updated in ThermoTrace">
            {p.updated_at ? new Date(p.updated_at).toLocaleString() : NA}
            {p.revision != null && ` · data revision ${p.revision}`}
          </DataRow>

          {tagEntries.length > 0 && (
            <>
              <SectionLabel>Source tags</SectionLabel>
              <div style={{ fontSize: 12, color: '#334155', lineHeight: 1.6 }}>
                {tagEntries.map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', gap: 8, borderBottom: '1px dashed #f1f5f9' }}>
                    <code style={{ color: '#64748b', flex: '0 0 40%', wordBreak: 'break-all' }}>{k}</code>
                    <span style={{ wordBreak: 'break-word' }}>{v}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </aside>
  );
}
