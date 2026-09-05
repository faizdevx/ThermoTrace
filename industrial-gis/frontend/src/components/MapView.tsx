import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import 'leaflet.markercluster/dist/MarkerCluster.css';
import 'leaflet.markercluster/dist/MarkerCluster.Default.css';
import 'leaflet.markercluster';
import type { FeatureCollection, IndustrialSiteFeature } from '@/types/industrial';

// ── Confidence colour map ────────────────────────────────────────────────────
const CONFIDENCE_COLORS: Record<string, string> = {
  high: '#22c55e',
  medium: '#f59e0b',
  low: '#ef4444',
};

// ── Industry icon map ────────────────────────────────────────────────────────
const INDUSTRY_ICONS: Record<string, string> = {
  logistics: '🚢', automotive: '🚗', engineering: '⚙️', textiles: '🧵',
  manufacturing: '🏭', petrochemicals: '🛢️', electronics: '💡', energy: '⚡',
  heavy_engineering: '🔩', foundry: '🔥', pharmaceuticals: '💊', fabrication: '🔧',
  mixed_manufacturing: '🏗️', chemicals: '⚗️', steel: '🏗️', food_processing: '🌾',
  marine: '⚓', metalworks: '🔨', renewables: '🌱', default: '🏭',
};

// ── Tear-drop marker icon ────────────────────────────────────────────────────
function makeIcon(confidence: string, industryType: string): L.DivIcon {
  const color = CONFIDENCE_COLORS[confidence] ?? '#64748b';
  const emoji = INDUSTRY_ICONS[industryType] ?? INDUSTRY_ICONS.default;
  return L.divIcon({
    className: '',
    html: `
      <div style="
        width: 38px; height: 38px;
        border-radius: 50% 50% 50% 0;
        transform: rotate(-45deg);
        background: ${color};
        border: 2.5px solid #fff;
        box-shadow: 0 3px 12px rgba(0,0,0,0.3);
        display: flex; align-items: center; justify-content: center;
      ">
        <span style="transform: rotate(45deg); font-size: 17px; line-height: 1;">${emoji}</span>
      </div>`,
    iconSize: [38, 38],
    iconAnchor: [19, 38],
    popupAnchor: [0, -42],
  });
}

// ── Popup HTML builder ───────────────────────────────────────────────────────
function buildPopupHtml(feature: IndustrialSiteFeature): string {
  const p = feature.properties;
  const confidenceColor = CONFIDENCE_COLORS[p.match_confidence] ?? '#64748b';
  const statusLabel = p.status.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  const statusColor = p.status === 'operational' ? '#15803d' : p.status === 'under_construction' ? '#b45309' : '#64748b';
  const statusBg = p.status === 'operational' ? '#f0fdf4' : p.status === 'under_construction' ? '#fffbeb' : '#f8fafc';
  const industryLabel = p.industry_type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  const scorePct = Math.round(p.match_score * 100);

  return `
    <div style="font-family:'IBM Plex Sans',sans-serif; min-width:230px; padding:2px 0;">
      <div style="font-size:13px;font-weight:700;color:#0f172a;line-height:1.35;margin-bottom:3px;">${p.name}</div>
      <div style="font-size:11px;color:#64748b;margin-bottom:10px;">${p.district}, ${p.state}</div>

      <div style="display:flex;gap:5px;flex-wrap:wrap;margin-bottom:8px;">
        <span style="font-size:10px;font-weight:600;padding:2px 8px;border-radius:99px;background:#f1f5f9;color:#475569;text-transform:uppercase;letter-spacing:0.05em;">
          ${industryLabel}
        </span>
        <span style="font-size:10px;font-weight:600;padding:2px 8px;border-radius:99px;background:${statusBg};color:${statusColor};text-transform:uppercase;letter-spacing:0.05em;">
          ${statusLabel}
        </span>
      </div>

      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
        <div style="flex:1;height:5px;border-radius:99px;background:#e2e8f0;overflow:hidden;">
          <div style="width:${scorePct}%;height:100%;background:${confidenceColor};border-radius:99px;"></div>
        </div>
        <span style="font-size:11px;font-weight:700;color:${confidenceColor};">${scorePct}%</span>
      </div>
      <div style="font-size:10px;color:#94a3b8;margin-bottom:10px;">
        Confidence: <span style="color:${confidenceColor};font-weight:600;text-transform:uppercase;">${p.match_confidence}</span>
      </div>

      <button
        onclick="window.__gisSelectSite && window.__gisSelectSite('${p.site_id}')"
        style="width:100%;padding:7px 0;border-radius:9px;background:#0f172a;color:#fff;font-size:11px;font-weight:600;border:none;cursor:pointer;letter-spacing:0.04em;transition:background 0.2s;"
        onmouseover="this.style.background='#1e293b'"
        onmouseout="this.style.background='#0f172a'"
      >View Full Details →</button>
    </div>`;
}

// ── Component ─────────────────────────────────────────────────────────────────

interface MapViewProps {
  sites: FeatureCollection<IndustrialSiteFeature> | null;
  selectedSiteId: string | null;
  onSelectSite: (siteId: string) => void;
  /** Called (debounced) whenever the visible map bounds change. Format: "minLon,minLat,maxLon,maxLat" */
  onBoundsChange?: (bbox: string) => void;
}

export function MapView({ sites, selectedSiteId, onSelectSite, onBoundsChange }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const clusterRef = useRef<L.MarkerClusterGroup | null>(null);
  const markerMapRef = useRef<Map<string, L.Marker>>(new Map());

  // Keep callback refs so listeners always call the latest version
  const onSelectSiteRef = useRef(onSelectSite);
  const onBoundsChangeRef = useRef(onBoundsChange);
  useEffect(() => { onSelectSiteRef.current = onSelectSite; }, [onSelectSite]);
  useEffect(() => { onBoundsChangeRef.current = onBoundsChange; }, [onBoundsChange]);

  // ── Initialise Leaflet map (once) ─────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      center: [22.5, 82.0],
      zoom: 5,
      zoomControl: false, // We'll add custom-positioned controls
    });

    // CartoDB Positron — clean light basemap
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors © <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: 'abcd',
      maxZoom: 19,
    }).addTo(map);

    // Custom-positioned zoom control (bottom right)
    L.control.zoom({ position: 'bottomright' }).addTo(map);

    // Cluster group with custom rendering
    const clusterGroup = (L as unknown as {
      markerClusterGroup: (opts?: object) => L.MarkerClusterGroup;
    }).markerClusterGroup({
      maxClusterRadius: 55,
      spiderfyOnMaxZoom: true,
      showCoverageOnHover: false,
      chunkedLoading: true,
      iconCreateFunction: (cluster: L.MarkerCluster) => {
        const count = cluster.getChildCount();
        const size = count < 10 ? 36 : count < 50 ? 44 : 52;
        const hue = count < 5 ? 201 : count < 15 ? 43 : 0; // blue → amber → red
        return L.divIcon({
          html: `
            <div style="
              width:${size}px; height:${size}px; border-radius:50%;
              background: hsla(${hue}, 90%, 50%, 0.18);
              border: 2px solid hsla(${hue}, 90%, 50%, 0.55);
              display:flex; align-items:center; justify-content:center;
            ">
              <span style="font-size:${size < 44 ? 12 : 14}px; font-weight:700; color:hsl(${hue},70%,35%);">${count}</span>
            </div>`,
          className: '',
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
        });
      },
    });

    map.addLayer(clusterGroup);
    mapRef.current = map;
    clusterRef.current = clusterGroup;

    // ── Bounds change listener (for bbox-based API calls) ─────────────────
    let boundsTimer: ReturnType<typeof setTimeout> | null = null;
    map.on('moveend', () => {
      if (boundsTimer) clearTimeout(boundsTimer);
      boundsTimer = setTimeout(() => {
        const b = map.getBounds();
        const bbox = [
          b.getWest().toFixed(4),
          b.getSouth().toFixed(4),
          b.getEast().toFixed(4),
          b.getNorth().toFixed(4),
        ].join(',');
        onBoundsChangeRef.current?.(bbox);
      }, 600);
    });

    return () => {
      if (boundsTimer) clearTimeout(boundsTimer);
      map.remove();
      mapRef.current = null;
      clusterRef.current = null;
    };
  }, []);

  // ── Expose select callback globally for popup button ─────────────────────
  useEffect(() => {
    (window as unknown as Record<string, unknown>).__gisSelectSite = (id: string) => {
      onSelectSiteRef.current(id);
    };
  }, []);

  // ── Sync markers whenever sites prop changes ──────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    const cluster = clusterRef.current;
    if (!map || !cluster) return;

    cluster.clearLayers();
    markerMapRef.current.clear();

    if (!sites || sites.features.length === 0) return;

    const latLngs: L.LatLng[] = [];

    sites.features.forEach(feature => {
      const [lon, lat] = feature.geometry.coordinates;
      const marker = L.marker([lat, lon], {
        icon: makeIcon(feature.properties.match_confidence, feature.properties.industry_type),
      });

      marker.bindPopup(buildPopupHtml(feature), { maxWidth: 300, minWidth: 240 });

      // Click → select site + open popup
      marker.on('click', () => {
        onSelectSiteRef.current(feature.properties.site_id);
      });

      cluster.addLayer(marker);
      markerMapRef.current.set(feature.properties.site_id, marker);
      latLngs.push(L.latLng(lat, lon));
    });

    // Fit map to visible markers
    if (latLngs.length > 0) {
      const bounds = L.latLngBounds(latLngs);
      map.fitBounds(bounds, { padding: [48, 48], maxZoom: 11, animate: true });
    }
  }, [sites]);

  // ── Pan to + highlight selected marker ────────────────────────────────────
  useEffect(() => {
    if (!selectedSiteId || !mapRef.current) return;
    const marker = markerMapRef.current.get(selectedSiteId);
    if (marker) {
      const currentZoom = mapRef.current.getZoom();
      mapRef.current.setView(marker.getLatLng(), Math.max(currentZoom, 11), { animate: true });
      setTimeout(() => marker.openPopup(), 350); // wait for animation
    }
  }, [selectedSiteId]);

  return (
    <div
      ref={containerRef}
      style={{ width: '100%', height: '100%', borderRadius: 'inherit' }}
    />
  );
}
