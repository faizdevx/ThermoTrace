import L from 'leaflet';
import { leafletLayer } from 'protomaps-leaflet';

export type BasemapConfig = { kind: 'pmtiles'; url: string; attribution: string } | { kind: 'raster'; url: string; attribution: string };

const ATTRIBUTION_DATA =
  'fire data <a href="https://firms.modaps.eosdis.nasa.gov/">NASA FIRMS</a> · land cover <a href="https://esa-worldcover.org/">ESA WorldCover</a>';

/**
 * Which basemap to draw. ``VITE_PMTILES_URL`` (for example ``/basemap/india.pmtiles``) selects the self-hosted vector basemap: one
 * PMTiles file served by this stack, read with HTTP range requests, no public tile server and no outbound call. Without it the
 * public OpenStreetMap raster tiles are used (``VITE_TILE_URL`` to point at another raster server).
 */
export function basemapConfig(env: { VITE_PMTILES_URL?: string; VITE_TILE_URL?: string } = import.meta.env as never): BasemapConfig {
  const pm = (env.VITE_PMTILES_URL || '').trim();
  if (pm) {
    return { kind: 'pmtiles', url: pm, attribution: `© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, <a href="https://protomaps.com">Protomaps</a> · ${ATTRIBUTION_DATA}` };
  }
  return {
    kind: 'raster', url: (env.VITE_TILE_URL || '').trim() || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: `© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · ${ATTRIBUTION_DATA}`,
  };
}

export function createBasemap(cfg: BasemapConfig = basemapConfig()): L.Layer {
  if (cfg.kind === 'pmtiles') {
    return leafletLayer({ url: cfg.url, flavor: 'light', lang: 'en', maxDataZoom: 12, maxZoom: 19, attribution: cfg.attribution }) as unknown as L.Layer;
  }
  return L.tileLayer(cfg.url, { attribution: cfg.attribution, maxZoom: 19 });
}
