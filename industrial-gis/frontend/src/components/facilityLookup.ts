import type { FacilityFeature } from '@/types/industrial';

export interface FacilityHit {
  id: string;
  name: string;
  kind: string;
  lat: number;
  lon: number;
}

const norm = (s: string) => s.toLowerCase().normalize('NFKD').replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();

export function prettyKind(kind: string): string {
  return kind.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

/** Client-side search over the already loaded facility layer: every word must occur in the name or kind;
 *  names that start with the query rank first. Returns at most `limit` hits. */
export function searchFacilities(features: FacilityFeature[] | undefined, query: string, limit = 8): FacilityHit[] {
  const words = norm(query).split(' ').filter(Boolean);
  if (!features || words.length === 0 || norm(query).length < 2) return [];
  const scored: { hit: FacilityHit; rank: number }[] = [];
  for (const f of features) {
    const name = f.properties.name?.trim();
    if (!name) continue;
    const hay = norm(`${name} ${f.properties.kind}`);
    if (!words.every(w => hay.includes(w))) continue;
    const [lon, lat] = f.geometry.coordinates;
    const rank = (norm(name).startsWith(words[0]) ? 0 : 1) * 1000 + name.length;
    scored.push({ hit: { id: f.properties.facility_id, name, kind: f.properties.kind, lat, lon }, rank });
  }
  scored.sort((a, b) => a.rank - b.rank);
  return scored.slice(0, limit).map(s => s.hit);
}

/** A small box around a point, used to fly the map to a facility. */
export function boxAround(lat: number, lon: number, half = 0.03): [number, number, number, number] {
  return [lon - half, lat - half, lon + half, lat + half];
}
