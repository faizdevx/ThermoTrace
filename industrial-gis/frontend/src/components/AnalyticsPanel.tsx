import type { StatisticsResponse } from '@/types/industrial';

interface AnalyticsPanelProps {
  statistics: StatisticsResponse | null;
  isOpen: boolean;
  onClose: () => void;
}

const BAR_COLORS = [
  '#0ea5e9', '#38bdf8', '#7dd3fc', '#bae6fd',
  '#22d3ee', '#06b6d4', '#0891b2', '#0e7490',
];

const INDUSTRY_COLORS: Record<string, string> = {
  automotive: '#f59e0b',
  electronics: '#a78bfa',
  logistics: '#34d399',
  pharmaceuticals: '#fb923c',
  food_processing: '#4ade80',
  manufacturing: '#60a5fa',
  textiles: '#f472b6',
  engineering: '#fbbf24',
  petrochemicals: '#ef4444',
  chemicals: '#c084fc',
  fabrication: '#2dd4bf',
  mixed_manufacturing: '#818cf8',
  energy: '#facc15',
  heavy_engineering: '#94a3b8',
  foundry: '#fb7185',
  steel: '#64748b',
  metalworks: '#475569',
  marine: '#06b6d4',
  renewables: '#22c55e',
};

function HorizontalBar({
  label,
  value,
  total,
  color,
}: {
  label: string;
  value: number;
  total: number;
  color: string;
}) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0;
  const barWidth = total > 0 ? (value / total) * 100 : 0;
  const readableLabel = label.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
        <span style={{ fontSize: 12, color: '#334155', fontWeight: 500, flexShrink: 0, maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {readableLabel}
        </span>
        <span style={{ fontSize: 12, color: '#64748b', marginLeft: 8, flexShrink: 0 }}>
          <strong style={{ color: '#0f172a' }}>{value}</strong>
          <span style={{ color: '#94a3b8', marginLeft: 4 }}>({pct}%)</span>
        </span>
      </div>
      <div style={{ height: 6, borderRadius: 99, background: '#f1f5f9', overflow: 'hidden' }}>
        <div
          style={{
            height: '100%',
            borderRadius: 99,
            background: `linear-gradient(90deg, ${color}cc, ${color})`,
            width: `${barWidth}%`,
            transition: 'width 0.5s cubic-bezier(0.34,1.56,0.64,1)',
          }}
        />
      </div>
    </div>
  );
}

export function AnalyticsPanel({ statistics, isOpen, onClose }: AnalyticsPanelProps) {
  const byState = statistics
    ? Object.entries(statistics.by_state).sort(([, a], [, b]) => b - a)
    : [];

  const byType = statistics
    ? Object.entries(statistics.by_industry_type).sort(([, a], [, b]) => b - a)
    : [];

  const totalSites = statistics?.total_sites ?? 0;

  return (
    <aside
      aria-label="Analytics panel"
      style={{
        position: 'fixed', top: 0, left: 0,
        width: 340, maxWidth: '100vw', height: '100vh',
        background: '#fff',
        boxShadow: '6px 0 48px rgba(15,23,42,0.14)',
        zIndex: 600,
        transform: isOpen ? 'translateX(0)' : 'translateX(-100%)',
        transition: 'transform 0.32s cubic-bezier(0.4,0,0.2,1)',
        display: 'flex', flexDirection: 'column',
        fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif",
        overflowY: 'auto',
      }}
    >
      {/* Header */}
      <div style={{
        background: 'linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%)',
        padding: '18px 20px 16px',
        flexShrink: 0,
        position: 'sticky', top: 0, zIndex: 1,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div>
          <p style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.14em', color: '#7dd3fc', margin: '0 0 4px' }}>
            Analytics
          </p>
          <h2 style={{ fontSize: 15, fontWeight: 700, color: '#f8fafc', margin: 0 }}>
            Distribution Breakdown
          </h2>
        </div>
        <button
          onClick={onClose}
          aria-label="Close analytics"
          style={{
            width: 32, height: 32, borderRadius: '50%',
            border: 'none', background: 'rgba(255,255,255,0.1)',
            color: '#fff', fontSize: 18, lineHeight: 1,
            cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
            transition: 'background 0.2s',
          }}
          onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.2)')}
          onMouseLeave={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.1)')}
        >
          ×
        </button>
      </div>

      {/* Body */}
      <div style={{ padding: '16px 20px 28px', flex: 1 }}>
        {!statistics ? (
          <div style={{ color: '#94a3b8', fontSize: 13, textAlign: 'center', padding: '40px 0' }}>
            Loading analytics…
          </div>
        ) : (
          <>
            {/* Summary pills */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 20, paddingBottom: 16, borderBottom: '1px solid #f1f5f9' }}>
              {[
                { label: 'Total Sites', value: statistics.total_sites, color: '#0ea5e9' },
                { label: 'States', value: byState.length, color: '#8b5cf6' },
                { label: 'Industries', value: byType.length, color: '#f59e0b' },
              ].map(pill => (
                <div key={pill.label} style={{
                  flex: '1 1 auto', textAlign: 'center',
                  padding: '8px 12px', borderRadius: 12,
                  background: '#f8fafc', border: '1px solid #f1f5f9',
                }}>
                  <p style={{ fontSize: 18, fontWeight: 800, color: pill.color, margin: '0 0 2px' }}>{pill.value}</p>
                  <p style={{ fontSize: 10, fontWeight: 600, color: '#94a3b8', margin: 0, textTransform: 'uppercase', letterSpacing: '0.07em' }}>{pill.label}</p>
                </div>
              ))}
            </div>

            {/* By state */}
            <section style={{ marginBottom: 24 }}>
              <h3 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#64748b', margin: '0 0 12px' }}>
                🗺️ &nbsp;Sites by State
              </h3>
              {byState.map(([state, count], i) => (
                <HorizontalBar
                  key={state}
                  label={state}
                  value={count}
                  total={totalSites}
                  color={BAR_COLORS[i % BAR_COLORS.length]}
                />
              ))}
            </section>

            {/* By industry type */}
            <section>
              <h3 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#64748b', margin: '0 0 12px' }}>
                🏭 &nbsp;Sites by Industry
              </h3>
              {byType.map(([type, count]) => (
                <HorizontalBar
                  key={type}
                  label={type}
                  value={count}
                  total={totalSites}
                  color={INDUSTRY_COLORS[type] ?? '#94a3b8'}
                />
              ))}
            </section>

            {/* Data note */}
            <div style={{
              marginTop: 20, padding: '10px 12px', borderRadius: 10,
              background: '#f8fafc', border: '1px solid #e2e8f0',
            }}>
              <p style={{ fontSize: 11, color: '#64748b', margin: 0, lineHeight: 1.5 }}>
                📊 Data derived from synthetic dataset — Phase 0 milestone. Values will reflect live PostGIS pipeline after integration.
              </p>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
