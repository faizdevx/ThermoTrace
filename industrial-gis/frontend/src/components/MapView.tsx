import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import 'leaflet.markercluster/dist/MarkerCluster.css';
import 'leaflet.markercluster/dist/MarkerCluster.Default.css';
import 'leaflet.markercluster';
import { fetchFacility } from '@/services/api';
import { facilityPopupHtml } from '@/components/facilityPopup';
import type {
  BoundaryFeature,
  FeatureCollection,
  IndustrialSiteFeature,
  ThermalFeature,
} from '@/types/industrial';

// ── Evidence colour map (tag completeness; NOT official verification) ─────────
export const EVIDENCE_COLORS: Record<string, string> = {
  high: '#22c55e',
  medium: '#f59e0b',
  low: '#ef4444',
};

const INDUSTRY_ICON_RULES: Array<[RegExp, string]> = [
  [/chemical|petro/i, '⚗️'], [/metal|steel/i, '🔩'], [/mining|quarr/i, '⛏️'],
  [/cement|building material/i, '🧱'], [/textile|garment/i, '🧵'], [/food/i, '🌾'],
  [/energy|power/i, '⚡'], [/warehouse|logistic/i, '📦'], [/wood|paper/i, '🪵'],
  [/waste|recycl/i, '♻️'], [/water|utilit/i, '💧'], [/industrial area/i, '▦'],
  [/building/i, '🏢'], [/manufactur/i, '🏭'],
];

function iconFor(industryType: string): string {
  for (const [rule, emoji] of INDUSTRY_ICON_RULES) if (rule.test(industryType)) return emoji;
  return '🏭';
}

function makeIcon(evidence: string, industryType: string, selected: boolean): L.DivIcon {
  const color = EVIDENCE_COLORS[evidence] ?? '#64748b';
  const size = selected ? 44 : 36;
  return L.divIcon({
    className: '',
    html: `<div style="width:${size}px;height:${size}px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);` +
      `background:${color};border:${selected ? 3 : 2.5}px solid ${selected ? '#0f172a' : '#fff'};` +
      `box-shadow:0 3px 12px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:center;">` +
      `<span style="transform:rotate(45deg);font-size:${selected ? 19 : 16}px;line-height:1;">${iconFor(industryType)}</span></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size],
    popupAnchor: [0, -size - 4],
  });
}

function label(value: string): string {
  return value ? value.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : '';
}

/** Popup built with DOM APIs + textContent: OSM names/tags are untrusted, never injected as HTML. */
function buildPopup(feature: IndustrialSiteFeature, onOpen: (id: string) => void): HTMLElement {
  const p = feature.properties;
  const root = document.createElement('div');
  root.style.cssText = "font-family:'IBM Plex Sans',sans-serif;min-width:220px;";

  const title = document.createElement('div');
  title.style.cssText = 'font-size:13px;font-weight:700;color:#0f172a;line-height:1.35;margin-bottom:3px;';
  title.textContent = p.name || `Unnamed ${p.industry_type || 'industrial site'}`;
  root.appendChild(title);

  const where = document.createElement('div');
  where.style.cssText = 'font-size:11px;color:#64748b;margin-bottom:8px;';
  where.textContent = [p.district, p.state].filter(Boolean).join(', ') || 'Administrative area unassigned';
  root.appendChild(where);

  const chips = document.createElement('div');
  chips.style.cssText = 'display:flex;gap:5px;flex-wrap:wrap;margin-bottom:8px;';
  const evidence = p.evidence_level ?? 'low';
  for (const [text, bg, fg] of [
    [label(p.industry_type), '#f1f5f9', '#475569'],
    [`Mapped status: ${label(p.status) || 'unknown'}`, '#f8fafc', '#64748b'],
    [`Evidence: ${evidence}`, `${EVIDENCE_COLORS[evidence] ?? '#64748b'}22`, EVIDENCE_COLORS[evidence] ?? '#64748b'],
  ] as const) {
    const chip = document.createElement('span');
    chip.style.cssText = `font-size:10px;font-weight:600;padding:2px 8px;border-radius:99px;background:${bg};color:${fg};`;
    chip.textContent = text;
    chips.appendChild(chip);
  }
  root.appendChild(chips);

  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = 'View details →';
  button.style.cssText =
    'width:100%;padding:7px 0;border-radius:9px;background:#0f172a;color:#fff;font-size:11px;font-weight:600;border:none;cursor:pointer;';
  button.addEventListener('click', () => onOpen(p.site_id));
  root.appendChild(button);
  return root;
}

export interface MapViewport {
  bbox: string;
  zoom: number;
}

export interface FitRequest {
  id: number;
  bounds: [number, number, number, number]; // west, south, east, north
}

interface MapViewProps {
  sites: FeatureCollection<IndustrialSiteFeature> | null;
  selectedSiteId: string | null;
  /** Full-geometry feature of the selected site (outline is drawn for polygons). */
  selectedSite: IndustrialSiteFeature | null;
  stateBoundaries: FeatureCollection<BoundaryFeature> | null;
  districtBoundaries: FeatureCollection<BoundaryFeature> | null;
  fitRequest: FitRequest | null;
  onSelectSite: (siteId: string) => void;
  onViewportChange?: (viewport: MapViewport) => void;
  /** Classified FIRMS thermal sources (null = layer hidden). */
  thermal?: FeatureCollection<ThermalFeature> | null;
  thermalColors?: Record<string, string>;
  selectedThermalId?: string | null;
  onSelectThermal?: (sourceId: string) => void;
  /** Merged facility database (null = layer hidden). */
  facilities?: import('@/types/industrial').FacilityCollection | null;
  /** Draw the OSM industrial-site markers (default true; the app starts with them off). */
  showSites?: boolean;
}

export function thermalRadius(frpMax: number): number {
  return Math.min(18, Math.max(4, 3.5 + 1.7 * Math.sqrt(Math.max(frpMax, 0))));
}

export function MapView({
  sites: allSites, selectedSiteId, selectedSite, stateBoundaries, districtBoundaries,
  fitRequest, onSelectSite, onViewportChange,
  thermal = null, thermalColors = {}, selectedThermalId = null, onSelectThermal, showSites = true, facilities = null,
}: MapViewProps) {
  const sites = showSites ? allSites : null;
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const clusterRef = useRef<L.MarkerClusterGroup | null>(null);
  const markersRef = useRef<Map<string, { marker: L.Marker; sig: string }>>(new Map());
  const outlineRef = useRef<L.GeoJSON | null>(null);
  const stateLayerRef = useRef<L.GeoJSON | null>(null);
  const districtLayerRef = useRef<L.GeoJSON | null>(null);
  const selectedIdRef = useRef<string | null>(selectedSiteId);
  const thermalLayerRef = useRef<L.LayerGroup | null>(null);
  const facilityLayerRef = useRef<L.LayerGroup | null>(null);
  const facilityRendererRef = useRef<L.Renderer | null>(null);
  const footprintLayerRef = useRef<L.LayerGroup | null>(null);
  const thermalMarkersRef = useRef<Map<string, { marker: L.CircleMarker; sig: string }>>(new Map());
  const onSelectThermalRef = useRef(onSelectThermal);
  useEffect(() => { onSelectThermalRef.current = onSelectThermal; }, [onSelectThermal]);

  const onSelectRef = useRef(onSelectSite);
  const onViewportRef = useRef(onViewportChange);
  useEffect(() => { onSelectRef.current = onSelectSite; }, [onSelectSite]);
  useEffect(() => { onViewportRef.current = onViewportChange; }, [onViewportChange]);
  useEffect(() => { selectedIdRef.current = selectedSiteId; }, [selectedSiteId]);

  // ── Initialise Leaflet map (once) ─────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, { center: [24.5, 80.5], zoom: 6, zoomControl: false });
    map.createPane('boundaries').style.zIndex = '350';
    map.createPane('outline').style.zIndex = '450';
    map.createPane('thermal').style.zIndex = '420';
    thermalLayerRef.current = L.layerGroup([], { pane: 'thermal' }).addTo(map);
    map.createPane('facilities').style.zIndex = '410';
    facilityLayerRef.current = L.layerGroup([], { pane: 'facilities' }).addTo(map);
    footprintLayerRef.current = L.layerGroup([], { pane: 'facilities' }).addTo(map);
    map.on('popupclose', () => footprintLayerRef.current?.clearLayers());

    // Basemap: OpenStreetMap standard tiles (https://operations.osmfoundation.org/policies/tiles/ - fine for
    // light use; set VITE_TILE_URL to your own tile provider for production traffic). Map DATA: OSM (ODbL).
    const tileUrl = (import.meta.env.VITE_TILE_URL as string | undefined) ?? 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
    L.tileLayer(tileUrl, {
      attribution:
        '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · ' +
        'fire data <a href="https://firms.modaps.eosdis.nasa.gov/">NASA FIRMS</a> · ' +
        'land cover <a href="https://esa-worldcover.org/">ESA WorldCover</a>',
      maxZoom: 19,
    }).addTo(map);
    L.control.zoom({ position: 'bottomright' }).addTo(map);

    const clusterGroup = (L as unknown as {
      markerClusterGroup: (opts?: object) => L.MarkerClusterGroup;
    }).markerClusterGroup({
      maxClusterRadius: 55,
      spiderfyOnMaxZoom: true,
      showCoverageOnHover: false,
      chunkedLoading: true,
      iconCreateFunction: (cluster: L.MarkerCluster) => {
        const count = cluster.getChildCount();
        const size = count < 10 ? 36 : count < 100 ? 44 : 52;
        const hue = count < 10 ? 201 : count < 100 ? 43 : 0;
        return L.divIcon({
          html: `<div style="width:${size}px;height:${size}px;border-radius:50%;background:hsla(${hue},90%,50%,0.18);` +
            `border:2px solid hsla(${hue},90%,50%,0.55);display:flex;align-items:center;justify-content:center;">` +
            `<span style="font-size:${size < 44 ? 12 : 14}px;font-weight:700;color:hsl(${hue},70%,35%);">${count}</span></div>`,
          className: '',
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
        });
      },
    });
    map.addLayer(clusterGroup);
    mapRef.current = map;
    clusterRef.current = clusterGroup;

    const emitViewport = () => {
      const b = map.getBounds();
      onViewportRef.current?.({
        bbox: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map(v => v.toFixed(4)).join(','),
        zoom: map.getZoom(),
      });
    };
    map.on('moveend', emitViewport);
    map.whenReady(emitViewport);

    // Leaflet needs a size recalculation when the container resizes (responsive layout).
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(containerRef.current);

    const markers = markersRef.current;
    return () => {
      observer.disconnect();
      map.remove();
      mapRef.current = null;
      clusterRef.current = null;
      markers.clear();
    };
  }, []);

  // ── Reconcile markers incrementally (no flicker, popup + map position preserved) ──
  useEffect(() => {
    const cluster = clusterRef.current;
    if (!cluster) return;
    const existing = markersRef.current;
    const incoming = new Map<string, IndustrialSiteFeature>();
    for (const f of sites?.features ?? []) {
      if (f.geometry.type === 'Point') incoming.set(f.properties.site_id, f);
    }

    const toRemove: L.Layer[] = [];
    for (const [id, entry] of existing) {
      if (!incoming.has(id)) { toRemove.push(entry.marker); existing.delete(id); }
    }
    if (toRemove.length) cluster.removeLayers(toRemove);

    const toAdd: L.Marker[] = [];
    for (const [id, feature] of incoming) {
      const p = feature.properties;
      const sig = `${p.revision ?? 0}|${p.evidence_level}|${p.industry_type}|${p.name}`;
      const prev = existing.get(id);
      if (prev && prev.sig === sig) continue;       // unchanged: keep the very same marker
      if (prev) cluster.removeLayer(prev.marker);   // changed: replace
      const [lon, lat] = (feature.geometry as { coordinates: [number, number] }).coordinates;
      const marker = L.marker([lat, lon], {
        icon: makeIcon(p.evidence_level ?? 'low', p.industry_type, id === selectedIdRef.current),
        title: p.name || undefined,
      });
      marker.bindPopup(() => buildPopup(feature, sid => onSelectRef.current(sid)), { maxWidth: 300, minWidth: 230 });
      marker.on('click', () => onSelectRef.current(id));
      existing.set(id, { marker, sig });
      toAdd.push(marker);
    }
    if (toAdd.length) cluster.addLayers(toAdd);
  }, [sites]);

  // ── Thermal sources: canvas circle markers, reconciled by source id (no flicker on live refresh) ──
  useEffect(() => {
    const layer = thermalLayerRef.current;
    const map = mapRef.current;
    if (!layer || !map) return;
    const existing = thermalMarkersRef.current;
    const incoming = new Map<string, ThermalFeature>();
    for (const f of thermal?.features ?? []) incoming.set(f.properties.source_id, f);

    for (const [id, entry] of existing) {
      if (!incoming.has(id)) { layer.removeLayer(entry.marker); existing.delete(id); }
    }
    const renderer = (map as unknown as { _thermalRenderer?: L.Renderer })._thermalRenderer
      ?? ((map as unknown as { _thermalRenderer?: L.Renderer })._thermalRenderer = L.canvas({ pane: 'thermal', padding: 0.3 }));
    // draw weak sources first so strong ones stay on top
    const ordered = [...incoming.values()].sort((a, b) => a.properties.frp_max - b.properties.frp_max);
    for (const f of ordered) {
      const p = f.properties;
      const sig = `${p.last_seen}|${p.thermal_class}|${p.frp_max}|${p.alert_level}|${p.n_days}`;
      const prev = existing.get(p.source_id);
      if (prev && prev.sig === sig) continue;
      if (prev) layer.removeLayer(prev.marker);
      const color = thermalColors[p.thermal_class] ?? '#64748b';
      const [lon, lat] = f.geometry.coordinates;
      const marker = L.circleMarker([lat, lon], {
        renderer, pane: 'thermal', radius: thermalRadius(p.frp_max), color: p.alert_level ? '#7f1d1d' : '#ffffff',
        weight: p.alert_level ? 3 : 1, fillColor: color, fillOpacity: 0.75,
      });
      marker.bindTooltip(
        `${p.thermal_label} - ${p.frp_max.toFixed(1)} MW max, ${p.n_days} day(s)${p.site_within && p.site_name ? ` - ${p.site_name}` : ''}`,
        { sticky: true });
      marker.on('click', () => onSelectThermalRef.current?.(p.source_id));
      layer.addLayer(marker);
      existing.set(p.source_id, { marker, sig });
    }
  }, [thermal, thermalColors]);

  useEffect(() => {
    const layer = facilityLayerRef.current;
    const map = mapRef.current;
    if (!layer || !map) return;
    layer.clearLayers();
    footprintLayerRef.current?.clearLayers();
    if (!facilities?.features.length) return;
    // one canvas renderer for the layer's lifetime (a new one per update would leak canvases)
    const renderer = facilityRendererRef.current
      ?? (facilityRendererRef.current = L.canvas({ pane: 'facilities', padding: 0.3 }));
    const controller = new AbortController();
    for (const f of facilities.features) {
      const p = f.properties;
      const [lon, lat] = f.geometry.coordinates;
      const multi = p.source_count > 1;
      const marker = L.circleMarker([lat, lon], {
        renderer, pane: 'facilities', radius: multi ? 4 : 3, weight: 1, color: '#1e293b',
        fillColor: multi ? '#0ea5e9' : '#94a3b8', fillOpacity: 0.85,
      }).bindTooltip(
        `${p.name ?? 'Unnamed facility'} - ${p.kind.replace(/_/g, ' ')}${p.capacity_mw ? ` - ${Math.round(p.capacity_mw)} MW` : ''}`
        + ` | sources: ${p.sources.split(',').join(', ')}`,
      );
      marker.on('click', () => {
        fetchFacility(p.facility_id, controller.signal).then(detail => {
          const outline = footprintLayerRef.current;
          outline?.clearLayers();
          if (detail.footprint && outline) {
            L.geoJSON(detail.footprint as GeoJSON.GeoJsonObject, {
              style: { color: '#0369a1', weight: 2, fillOpacity: 0.08, interactive: false },
            }).addTo(outline);
          }
          L.popup({ maxWidth: 300 }).setLatLng([lat, lon]).setContent(facilityPopupHtml(detail)).openOn(map);
        }).catch(() => { /* aborted or offline: tooltip still shows the basics */ });
      });
      marker.addTo(layer);
    }
    return () => controller.abort();
  }, [facilities]);

  useEffect(() => {
    for (const [id, { marker }] of thermalMarkersRef.current) {
      const selected = id === selectedThermalId;
      marker.setStyle({ weight: selected ? 4 : marker.options.weight, color: selected ? '#0f172a' : marker.options.color });
      if (selected) marker.bringToFront();
    }
  }, [selectedThermalId, thermal]);

  // ── Highlight the selected marker and draw its true geometry ─────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    for (const [id, { marker }] of markersRef.current) {
      const p = sites?.features.find(f => f.properties.site_id === id)?.properties;
      if (p) marker.setIcon(makeIcon(p.evidence_level ?? 'low', p.industry_type, id === selectedSiteId));
    }
    // sites intentionally omitted: icons for new markers are already correct; this only re-styles on selection
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSiteId]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    outlineRef.current?.remove();
    outlineRef.current = null;
    if (selectedSite && selectedSite.properties.site_id === selectedSiteId && selectedSite.geometry.type !== 'Point') {
      outlineRef.current = L.geoJSON(selectedSite as unknown as GeoJSON.Feature, {
        pane: 'outline',
        style: { color: '#0f172a', weight: 2.5, fillColor: '#0ea5e9', fillOpacity: 0.22 },
      }).addTo(map);
    }
  }, [selectedSite, selectedSiteId]);

  // Pan to a newly selected site (only when it is not already comfortably in view)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !selectedSite || selectedSite.properties.site_id !== selectedSiteId) return;
    const { latitude, longitude } = selectedSite.properties;
    if (latitude == null || longitude == null) return;
    if (!map.getBounds().pad(-0.15).contains([latitude, longitude])) {
      map.setView([latitude, longitude], Math.max(map.getZoom(), 11), { animate: true });
    }
    // only react to which site is selected, not to its refreshed data
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSiteId]);

  // ── Boundary layers ───────────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    stateLayerRef.current?.remove();
    stateLayerRef.current = null;
    if (stateBoundaries && stateBoundaries.features.length) {
      stateLayerRef.current = L.geoJSON(stateBoundaries as unknown as GeoJSON.FeatureCollection, {
        pane: 'boundaries', interactive: false,
        style: { color: '#1d4ed8', weight: 1.5, fill: false, opacity: 0.75 },
      }).addTo(map);
    }
  }, [stateBoundaries]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    districtLayerRef.current?.remove();
    districtLayerRef.current = null;
    if (districtBoundaries && districtBoundaries.features.length) {
      districtLayerRef.current = L.geoJSON(districtBoundaries as unknown as GeoJSON.FeatureCollection, {
        pane: 'boundaries',
        style: { color: '#7c3aed', weight: 1, fill: false, opacity: 0.6, dashArray: '4 3' },
        onEachFeature: (feature, layer) => {
          const name = (feature.properties as { name?: string })?.name;
          if (name) layer.bindTooltip(name, { sticky: true });
        },
      }).addTo(map);
    }
  }, [districtBoundaries]);

  // ── Explicit fit requests (state/district chosen, reset) - never on data refresh ──
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !fitRequest) return;
    const [w, s, e, n] = fitRequest.bounds;
    map.fitBounds([[s, w], [n, e]], { padding: [32, 32], maxZoom: 11, animate: true });
  }, [fitRequest]);

  return <div ref={containerRef} style={{ width: '100%', height: '100%', borderRadius: 'inherit' }} />;
}
