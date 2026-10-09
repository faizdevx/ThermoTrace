import { vi } from 'vitest';

type Listener = (ev: MessageEvent) => void;

/** Minimal controllable EventSource for tests (jsdom has none). */
export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static CLOSED = 2;
  static OPEN = 1;
  static CONNECTING = 0;
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, Listener[]>();

  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(name: string, fn: Listener) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }

  close() {
    this.readyState = 2;
  }

  // ── test controls ──
  open() {
    this.readyState = 1;
    this.onopen?.();
  }

  emit(name: string, data: unknown) {
    for (const fn of this.listeners.get(name) ?? []) {
      fn({ data: JSON.stringify(data) } as MessageEvent);
    }
  }

  fail() {
    this.onerror?.();
  }

  static reset() {
    FakeEventSource.instances = [];
  }

  static get last(): FakeEventSource {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1];
  }
}

export function installFakeEventSource() {
  FakeEventSource.reset();
  vi.stubGlobal('EventSource', FakeEventSource);
}

export function site(id: string, overrides: Record<string, unknown> = {}, coords: [number, number] = [91.7, 26.1]) {
  return {
    type: 'Feature' as const,
    geometry: { type: 'Point' as const, coordinates: coords },
    properties: {
      site_id: id, name: `Site ${id}`, normalized_name: `site ${id}`, industry_type: 'Manufacturing',
      normalized_industry_type: 'manufacturing', status: 'mapped', state: 'Assam', district: 'Kamrup',
      address: '', latitude: coords[1], longitude: coords[0], osm_ids: [`way/${id}`], government_ids: [],
      source_ids: [`osm:way/${id}`], data_sources: ['openstreetmap'], source_count: 1, match_score: 0,
      match_confidence: 'unmatched', match_method: 'osm_only', review_required: false, last_verified: '',
      evidence_level: 'medium', evidence_score: 2, feature_class: 'facility', geometry_type: 'POINT',
      primary_source: 'openstreetmap', source_ref: `way/${id}`, source_url: `https://www.openstreetmap.org/way/${id}`,
      revision: 1, ...overrides,
    },
  };
}
