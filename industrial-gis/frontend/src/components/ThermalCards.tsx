import type { FacilityCollection, FeatureCollection, ThermalFeature, ThermalStats } from '@/types/industrial';

/** Headline numbers for the default (thermal + facilities) view. The OSM-pilot cards replace these only when
 *  the pilot layer is switched on, so the header never shows a row of zeros for a dataset the map is not drawing. */
export interface ThermalCardValues {
  sources: number | null;
  alerts: number | null;
  persistent: number | null;
  fireCandidates: number | null;
  newHeat: number | null;
  facilities: number | null;
  multiSource: number | null;
}

export function thermalCardValues(stats: ThermalStats | null, data: FeatureCollection<ThermalFeature> | null,
  facilities: FacilityCollection | null): ThermalCardValues {
  const withBaseline = data?.features.filter(f => f.properties.baseline_scope != null) ?? [];
  return {
    sources: stats?.total_sources ?? null,
    alerts: stats ? stats.alerts.watch + stats.alerts.high + stats.alerts.critical : null,
    persistent: stats ? stats.by_class.industrial_persistent ?? 0 : null,
    fireCandidates: stats ? stats.by_class.industrial_fire_candidate ?? 0 : null,
    // only meaningful once a baseline exists; null keeps the card on "-" instead of a misleading 0
    newHeat: withBaseline.length ? withBaseline.filter(f => !f.properties.baseline_known).length : null,
    facilities: facilities?.meta.available ? facilities.meta.total : null,
    multiSource: facilities?.meta.available ? facilities.features.filter(f => f.properties.source_count > 1).length : null,
  };
}

const CARDS: { key: keyof ThermalCardValues; label: string; icon: string; color: string; bg: string; border: string; hint: string }[] = [
  { key: 'sources', label: 'Thermal sources', icon: '🔥', color: '#ea580c', bg: 'linear-gradient(135deg, #fff7ed 0%, #ffedd5 100%)', border: '#fed7aa',
    hint: 'Classified NASA FIRMS sources matching the thermal filters' },
  { key: 'alerts', label: 'Alerts', icon: '🚨', color: '#dc2626', bg: 'linear-gradient(135deg, #fef2f2 0%, #fee2e2 100%)', border: '#fecaca',
    hint: 'Open alerts in the inbox (a source that looks like an industrial fire or runs far above its facility baseline); the thermal-source flag when no inbox is available' },
  { key: 'persistent', label: 'Persistent industrial', icon: '🏭', color: '#b91c1c', bg: 'linear-gradient(135deg, #fff1f2 0%, #ffe4e6 100%)', border: '#fecdd3',
    hint: 'Recurring heat at a known facility' },
  { key: 'fireCandidates', label: 'Possible industrial fire', icon: '⚠️', color: '#c2410c', bg: 'linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%)', border: '#fde68a',
    hint: 'Episodic heat at a facility - verify with imagery' },
  { key: 'newHeat', label: 'New heat (no 365-day history)', icon: '🆕', color: '#7c3aed', bg: 'linear-gradient(135deg, #faf5ff 0%, #ede9fe 100%)', border: '#ddd6fe',
    hint: 'Sources at places with no VIIRS detection in the 365-day baseline' },
  { key: 'facilities', label: 'Facilities', icon: '⚙️', color: '#0369a1', bg: 'linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%)', border: '#bfdbfe',
    hint: 'Merged facility database (OSM, WRI, GEM, flare sites)' },
  { key: 'multiSource', label: 'Confirmed by 2+ sources', icon: '✅', color: '#059669', bg: 'linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%)', border: '#a7f3d0',
    hint: 'Facilities that more than one independent source lists' },
];

export function ThermalCards({ values }: { values: ThermalCardValues }) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'stretch', overflowX: 'auto', paddingBottom: 2 }} data-testid="thermal-cards">
      {CARDS.map(c => (
        <article key={c.key} title={c.hint} style={{
          flex: '1 1 110px', minWidth: 100, padding: '7px 12px', borderRadius: 12, background: c.bg,
          border: `1px solid ${c.border}`, display: 'flex', flexDirection: 'column', gap: 2,
        }}>
          <p data-testid={`card-${c.key}`} style={{ fontSize: 20, fontWeight: 800, color: c.color, margin: 0, lineHeight: 1.1,
            letterSpacing: '-0.02em', display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 14 }} aria-hidden>{c.icon}</span>
            {values[c.key] != null ? (values[c.key] as number).toLocaleString() : '—'}
          </p>
          <p style={{ fontSize: 10, fontWeight: 600, color: c.color, opacity: 0.8, margin: 0, whiteSpace: 'nowrap',
            overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.label}</p>
        </article>
      ))}
    </div>
  );
}
