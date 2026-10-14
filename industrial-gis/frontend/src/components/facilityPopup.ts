import type { FacilityDetail } from '@/types/industrial';

const SOURCE_LABEL: Record<string, string> = {
  osm: 'OpenStreetMap', wri_gppd: 'WRI Power Plant DB', gem: 'Global Energy Monitor', vnf: 'VIIRS Nightfire',
};

export function escapeHtml(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, ch => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch] as string));
}

function safeUrl(url: string | null): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null;
}

/** Popup body for one facility: attributes plus every merged source record with its licence. */
export function facilityPopupHtml(d: FacilityDetail): string {
  const p = d.properties;
  const facts = [
    p.kind.replace(/_/g, ' '),
    p.capacity_mw ? `${Math.round(p.capacity_mw)} MW` : null,
    p.fuel ? `fuel: ${p.fuel}` : null,
    p.status ? `status: ${p.status}` : null,
  ].filter(Boolean).map(escapeHtml).join(' &middot; ');
  const rows = d.source_records.map(r => {
    const url = safeUrl(r.url);
    const id = url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(r.source_id)}</a>`
      : escapeHtml(r.source_id);
    const dist = r.distance_m ? ` &middot; ${Math.round(r.distance_m)} m` : '';
    return `<li><b>${escapeHtml(SOURCE_LABEL[r.source] ?? r.source)}</b> ${id}${r.name ? ` &ndash; ${escapeHtml(r.name)}` : ''}`
      + `${dist}<br><span style="color:#64748b">${escapeHtml(r.licence)}</span></li>`;
  }).join('');
  return `<div style="font-size:12px;max-width:260px">`
    + `<div style="font-weight:700">${escapeHtml(p.name ?? 'Unnamed facility')}</div>`
    + `<div style="color:#475569;margin:2px 0 6px">${facts}</div>`
    + `<div style="font-size:11px;font-weight:600">Sources (${d.source_records.length})</div>`
    + `<ul style="margin:2px 0 0;padding-left:16px;font-size:11px">${rows}</ul>`
    + (d.footprint ? '<div style="font-size:10px;color:#64748b;margin-top:4px">Outline: OpenStreetMap footprint</div>' : '')
    + `</div>`;
}
