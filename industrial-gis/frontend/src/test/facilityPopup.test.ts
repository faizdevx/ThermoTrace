import { describe, expect, it } from 'vitest';
import { escapeHtml, facilityPopupHtml } from '@/components/facilityPopup';
import type { FacilityDetail } from '@/types/industrial';

const detail = (over: Partial<FacilityDetail> = {}): FacilityDetail => ({
  type: 'Feature',
  geometry: { type: 'Point', coordinates: [83, 21] },
  properties: {
    facility_id: 'f1', name: 'Jindal <Power>', kind: 'power_thermal', radius_m: 1500, capacity_mw: 1199.6,
    status: 'operating', fuel: 'Coal', primary_source: 'gem', source_count: 2, sources: 'gem,osm', attribution: 'x',
  },
  footprint: null,
  source_records: [
    { facility_id: 'f1', source: 'gem', source_id: 'coal_plant:L1', name: null, distance_m: 0, url: null,
      licence: 'CC-BY-4.0', attribution: 'Global Energy Monitor' },
    { facility_id: 'f1', source: 'osm', source_id: 'way/9', name: 'TPS', distance_m: 212.4,
      url: 'https://www.openstreetmap.org/way/9', licence: 'ODbL-1.0', attribution: '(c) OpenStreetMap contributors' },
  ],
  ...over,
});

describe('facility popup', () => {
  it('lists every source with its licence and link', () => {
    const html = facilityPopupHtml(detail());
    expect(html).toContain('Sources (2)');
    expect(html).toContain('Global Energy Monitor');
    expect(html).toContain('ODbL-1.0');
    expect(html).toContain('href="https://www.openstreetmap.org/way/9"');
    expect(html).toContain('1200 MW');
    expect(html).toContain('212 m');
  });

  it('escapes names and refuses non-http links', () => {
    const html = facilityPopupHtml(detail({
      source_records: [{ facility_id: 'f1', source: 'osm', source_id: 'x', name: '<img onerror=1>', distance_m: null,
        url: 'javascript:alert(1)', licence: 'ODbL-1.0', attribution: 'o' }],
    }));
    expect(html).toContain('Jindal &lt;Power&gt;');
    expect(html).not.toContain('<img');
    expect(html).not.toContain('javascript:');
    expect(escapeHtml(`"'&`)).toBe('&quot;&#39;&amp;');
  });

  it('mentions the outline only when a footprint exists', () => {
    expect(facilityPopupHtml(detail())).not.toContain('Outline');
    expect(facilityPopupHtml(detail({ footprint: { type: 'Polygon', coordinates: [] } }))).toContain('Outline');
  });
});
