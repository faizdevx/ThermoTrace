import { describe, expect, it } from 'vitest';
import { boxAround, searchFacilities } from '@/components/facilityLookup';
import type { FacilityFeature } from '@/types/industrial';

const f = (id: string, name: string | null, kind: string, lon = 77, lat = 20): FacilityFeature => ({
  type: 'Feature', geometry: { type: 'Point', coordinates: [lon, lat] },
  properties: { facility_id: id, name, kind, radius_m: 500, capacity_mw: null, status: null, primary_source: 'osm', source_count: 1, sources: 'osm', attribution: '' },
});
const data = [
  f('1', 'Barauni Refinery', 'refinery'), f('2', 'Vatva GIDC Chemicals', 'chemical'),
  f('3', 'Bhilai Steel Plant', 'steel'), f('4', null, 'mine'), f('5', 'Refinery Road Depot', 'fuel_depot'),
];

describe('searchFacilities', () => {
  it('needs two characters and ignores unnamed sites', () => {
    expect(searchFacilities(data, 'b')).toEqual([]);
    expect(searchFacilities(data, 'mine')).toEqual([]);
  });
  it('requires every word and ranks prefix matches first', () => {
    expect(searchFacilities(data, 'steel plant').map(h => h.id)).toEqual(['3']);
    expect(searchFacilities(data, 'refinery').map(h => h.id)).toEqual(['5', '1']);
  });
  it('matches the kind and is case/punctuation insensitive', () => {
    expect(searchFacilities(data, 'CHEMICAL').map(h => h.id)).toEqual(['2']);
    expect(searchFacilities(data, 'vatva-gidc').map(h => h.id)).toEqual(['2']);
  });
  it('caps results and tolerates a missing layer', () => {
    expect(searchFacilities(undefined, 'steel')).toEqual([]);
    expect(searchFacilities(data, 're', 1)).toHaveLength(1);
  });
});

describe('boxAround', () => {
  it('returns west,south,east,north around the point', () => {
    expect(boxAround(20, 77, 0.5)).toEqual([76.5, 19.5, 77.5, 20.5]);
  });
});
