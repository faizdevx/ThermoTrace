import type { FeatureCollection, ThermalFeature } from '@/types/industrial';

/**
 * Static mode (VITE_STATIC_DATA=1, the GitHub Pages build): no API server. A scheduled job runs the real pipeline and API,
 * saves their answers as JSON files under `<base>/data/`, and this module answers the app's API paths from those files.
 * Thermal source filters are applied here, the same way the API applies them. Writes and per-item endpoints that were not
 * saved answer 404.
 */
export const STATIC_DATA = import.meta.env.VITE_STATIC_DATA === '1';
const DATA_BASE = `${import.meta.env.BASE_URL ?? '/'}data/`;

export class StaticMiss extends Error {}

const cache = new Map<string, Promise<unknown>>();
async function load<T>(file: string): Promise<T> {
  if (!cache.has(file)) {
    // shortcut: per-page-load cache; the app polls status.json and drops the cache when the snapshot changes
    cache.set(file, fetch(DATA_BASE + file, { cache: 'no-cache' }).then(r => {
      if (!r.ok) throw new StaticMiss(`${file}: HTTP ${r.status}`);
      return r.json();
    }));
    cache.get(file)!.catch(() => cache.delete(file));
  }
  return cache.get(file) as Promise<T>;
}

export function clearStaticCache(): void { cache.clear(); }

const EMPTY_FC = { type: 'FeatureCollection', features: [], meta: { total: 0, returned: 0, truncated: false } };

/** Same rules as backend/app/services/thermal_repo.py (file backend): classes, min power, alerts only, hours before the newest source. */
export function filterSources(fc: FeatureCollection<ThermalFeature>, q: URLSearchParams): FeatureCollection<ThermalFeature> {
  const classes = (q.get('thermal_class') ?? '').split(',').filter(Boolean);
  const minFrp = q.get('min_frp') ? Number(q.get('min_frp')) : null;
  const alertsOnly = q.get('alerts_only') === 'true';
  const hours = q.get('hours') ? Number(q.get('hours')) : null;
  const latest = Math.max(0, ...fc.features.map(f => Date.parse(f.properties.last_seen) || 0));
  const features = fc.features.filter(f => {
    const p = f.properties;
    if (classes.length && !classes.includes(p.thermal_class)) return false;
    if (minFrp !== null && p.frp_max < minFrp) return false;
    if (alertsOnly && !p.alert_level) return false;
    if (hours && latest - Date.parse(p.last_seen) > hours * 3600_000) return false;
    return true;
  });
  return { ...fc, features };
}

/** Answer an API path (`/thermal/sources?...`) from the saved files. */
export async function staticRequest<T>(path: string): Promise<T> {
  const [route, qs = ''] = path.split('?');
  const q = new URLSearchParams(qs);
  let m: RegExpMatchArray | null;

  if (route === '/thermal/sources') return filterSources(await load('thermal/sources.json'), q) as T;
  if ((m = route.match(/^\/thermal\/sources\/([^/]+)$/))) {
    const fc = await load<FeatureCollection<ThermalFeature>>('thermal/sources.json');
    const hit = fc.features.find(f => f.properties.source_id === decodeURIComponent(m![1]));
    if (!hit) throw new StaticMiss(`source ${m[1]} not in the snapshot`);
    return hit as T;
  }
  if (route === '/thermal/stats') {
    const h = q.get('hours');
    return load(h === '24' || h === '72' ? `thermal/stats_h${h}.json` : 'thermal/stats.json');
  }
  if ((m = route.match(/^\/alerts\/(\d+)$/))) return load(`alerts/${m[1]}.json`);
  if (route.startsWith('/boundaries/districts')) return EMPTY_FC as T;      // 21 MB national file: not published
  if (route.startsWith('/industries')) return EMPTY_FC as T;                 // OSM pilot layer: not published

  const files: Record<string, string> = {
    '/health': 'health.json', '/sync/status': 'sync_status.json', '/auth/me': 'auth_me.json',
    '/thermal/classes': 'thermal/classes.json', '/thermal/status': 'thermal/status.json',
    '/facilities': 'facilities.json', '/boundaries/states': 'boundaries_states.json', '/boundaries/india': 'boundaries_india.json',
    '/dashboard': 'dashboard.json', '/alerts': 'alerts.json', '/statistics': 'statistics.json', '/filters/options': 'filter_options.json',
  };
  if (files[route]) {
    const data = await load<T>(files[route]);
    // a state chosen in the pilot filters: keep only that state's outline
    if (route === '/boundaries/states' && q.get('state')) {
      const fc = data as unknown as { features: { properties: { name: string } }[] };
      return { ...fc, features: fc.features.filter(f => f.properties.name === q.get('state')) } as T;
    }
    return data;
  }
  throw new StaticMiss(`${route} is not available in the static site`);
}
