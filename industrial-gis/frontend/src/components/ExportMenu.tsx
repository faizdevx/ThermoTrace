import { useState } from 'react';
import { exportUrl } from '@/services/api';
import type { IndustryQueryFilters } from '@/types/industrial';

interface Props {
  filters: IndustryQueryFilters & { q?: string };
  viewportBbox: string | null;
  /** Number of sites the filters (without viewport) match, from /api/statistics. */
  totalMatching: number | null;
  disabled?: boolean;
}

const linkStyle: React.CSSProperties = {
  padding: '3px 10px', borderRadius: 8, border: '1px solid #cbd5e1', background: '#fff',
  color: '#0f172a', fontSize: 12, fontWeight: 600, textDecoration: 'none', cursor: 'pointer',
};

/**
 * CSV / GeoJSON export of EVERYTHING matching the current filters (server-side stream - not
 * limited to the loaded page). "Map view only" additionally restricts to the visible extent.
 */
export function ExportMenu({ filters, viewportBbox, totalMatching, disabled }: Props) {
  const [viewOnly, setViewOnly] = useState(false);
  const effective = { ...filters, ...(viewOnly && viewportBbox ? { bbox: viewportBbox } : {}) };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
      <span style={{ fontSize: 12, color: '#64748b' }}>
        Export {viewOnly ? 'map view' : totalMatching != null ? `${totalMatching.toLocaleString()} matching` : 'all matching'}:
      </span>
      {(['csv', 'geojson'] as const).map(format => (
        <a
          key={format}
          id={`export-${format}`}
          href={disabled ? undefined : exportUrl(format, effective)}
          download
          aria-disabled={disabled}
          style={{ ...linkStyle, opacity: disabled ? 0.5 : 1, pointerEvents: disabled ? 'none' : 'auto' }}
        >
          {format === 'csv' ? 'CSV' : 'GeoJSON'}
        </a>
      ))}
      <label style={{ fontSize: 12, color: '#64748b', display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
        <input type="checkbox" checked={viewOnly} onChange={e => setViewOnly(e.target.checked)} />
        map view only
      </label>
    </span>
  );
}
