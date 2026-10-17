import type { StatisticsResponse } from '@/types/industrial';

interface StatisticsCardsProps {
  statistics: StatisticsResponse | null;
  onShowAnalytics?: () => void;
}

interface StatDef {
  id: string;
  label: string;
  icon: string;
  color: string;
  bg: string;
  border: string;
  value: (s: StatisticsResponse) => number;
  hint: string;
}

// Every value is computed by the API over ACTIVE sites matching the current filters
// (state, district, industry, status, evidence, search) - never the map viewport.
const STAT_DEFS: StatDef[] = [
  { id: 'sites', label: 'Industrial Sites', icon: '🏭', color: '#0369a1', bg: 'linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%)', border: '#bfdbfe',
    value: s => s.total_sites, hint: 'Active mapped sites matching the filters' },
  { id: 'states', label: 'States', icon: '🗺️', color: '#7c3aed', bg: 'linear-gradient(135deg, #faf5ff 0%, #ede9fe 100%)', border: '#ddd6fe',
    value: s => s.distinct_states ?? Object.keys(s.by_state).length, hint: 'Distinct states containing matching sites' },
  { id: 'districts', label: 'Districts', icon: '📍', color: '#16a34a', bg: 'linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%)', border: '#bbf7d0',
    value: s => s.distinct_districts ?? Object.keys(s.by_district ?? {}).length, hint: 'Distinct districts containing matching sites' },
  { id: 'facilities', label: 'Facilities', icon: '⚙️', color: '#d97706', bg: 'linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%)', border: '#fde68a',
    value: s => s.by_feature_class?.facility ?? 0, hint: 'Tagged factories/works (not land-use zones)' },
  { id: 'high', label: 'High Evidence', icon: '✅', color: '#059669', bg: 'linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%)', border: '#a7f3d0',
    value: s => s.confidence_distribution.high ?? 0, hint: 'Well-tagged sites (calculated tag completeness, not verification)' },
  { id: 'gov', label: 'Govt. Records', icon: '🏛️', color: '#64748b', bg: 'linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%)', border: '#e2e8f0',
    value: s => s.government_records, hint: 'No government source is integrated yet' },
];

export function StatisticsCards({ statistics, onShowAnalytics }: StatisticsCardsProps) {
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'stretch', overflowX: 'auto', paddingBottom: 2 }}>
      {STAT_DEFS.map(def => (
        <article
          key={def.id} title={def.hint}
          style={{
            flex: '1 1 120px',
            minWidth: 110,
            padding: '10px 14px',
            borderRadius: 16,
            background: def.bg,
            border: `1px solid ${def.border}`,
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
            transition: 'transform 0.15s, box-shadow 0.15s',
            cursor: 'default',
          }}
          onMouseEnter={e => {
            (e.currentTarget as HTMLElement).style.transform = 'translateY(-2px)';
            (e.currentTarget as HTMLElement).style.boxShadow = `0 6px 20px ${def.border}88`;
          }}
          onMouseLeave={e => {
            (e.currentTarget as HTMLElement).style.transform = 'none';
            (e.currentTarget as HTMLElement).style.boxShadow = 'none';
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 16 }}>{def.icon}</span>
          </div>
          <p
            style={{
              fontSize: 22,
              fontWeight: 800,
              color: def.color,
              margin: 0,
              lineHeight: 1,
              letterSpacing: '-0.02em',
              fontFamily: "'IBM Plex Sans', sans-serif",
            }}
          >
            {statistics != null ? def.value(statistics).toLocaleString() : '—'}
          </p>
          <p
            style={{
              fontSize: 12,
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.07em',
              color: def.color,
              opacity: 0.75,
              margin: 0,
            }}
          >
            {def.label}
          </p>
        </article>
      ))}

      {/* Analytics button card */}
      <button
        onClick={onShowAnalytics}
        title="View distribution analytics"
        style={{
          flex: '0 0 auto',
          width: 110,
          padding: '10px 14px',
          borderRadius: 16,
          background: 'linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%)',
          border: '1px solid #1e3a5f',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 6,
          cursor: 'pointer',
          transition: 'transform 0.15s, box-shadow 0.15s',
          fontFamily: "'IBM Plex Sans', sans-serif",
        }}
        onMouseEnter={e => {
          (e.currentTarget as HTMLElement).style.transform = 'translateY(-2px)';
          (e.currentTarget as HTMLElement).style.boxShadow = '0 6px 20px rgba(14,165,233,0.25)';
        }}
        onMouseLeave={e => {
          (e.currentTarget as HTMLElement).style.transform = 'none';
          (e.currentTarget as HTMLElement).style.boxShadow = 'none';
        }}
      >
        <span style={{ fontSize: 20 }}>📊</span>
        <span style={{
          fontSize: 12, fontWeight: 700, textTransform: 'uppercase',
          letterSpacing: '0.07em', color: '#7dd3fc',
        }}>
          Analytics
        </span>
      </button>
    </div>
  );
}
