import { useEffect, useMemo, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import type { BoundaryFeature, FacilityCollection, FeatureCollection, ThermalClassInfo, ThermalFeature } from '@/types/industrial';
import { basemapConfig } from '@/components/basemap';
import { useNarrow } from '@/hooks/useNarrow';
import {
  BASES, GIBS_BLUE_MARBLE, GIBS_NIGHT, GLOBE_VIEWS, TERRAIN_TILES, gibsFires, gibsTrueColor, globeStats, isoDay, toGlobeGeoJSON,
  type GlobeBase,
} from '@/components/globeData';

interface Props {
  thermal: FeatureCollection<ThermalFeature> | null;
  classes: ThermalClassInfo[];
  facilities: FacilityCollection | null;
  states: FeatureCollection<BoundaryFeature> | null;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

const EMPTY = { type: 'FeatureCollection', features: [] } as const;
const ATTR = 'Imagery: NASA GIBS · Terrain: Mapzen/AWS · Fire data: NASA FIRMS';

function streetsUrl(): string {
  const c = basemapConfig();
  return c.kind === 'raster' ? c.url : 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
}

const panel: React.CSSProperties = {
  position: 'absolute', zIndex: 5, background: 'rgba(15,23,42,0.86)', color: '#e2e8f0', backdropFilter: 'blur(10px)',
  borderRadius: 14, boxShadow: '0 4px 24px rgba(0,0,0,0.4)', fontSize: 13,
};
const h: React.CSSProperties = { margin: '10px 0 4px', fontSize: 12, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: '#94a3b8' };
const chip = (on: boolean): React.CSSProperties => ({
  padding: '4px 10px', borderRadius: 99, border: '1px solid ' + (on ? '#38bdf8' : '#475569'), cursor: 'pointer', fontSize: 12, fontWeight: 600,
  background: on ? '#0369a1' : 'transparent', color: on ? '#fff' : '#cbd5e1',
});

export function GlobeView({ thermal, classes, facilities, states, selectedId, onSelect }: Props) {
  const narrow = useNarrow();
  const box = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  const [open, setOpen] = useState(!narrow);
  const [base, setBase] = useState<GlobeBase>('truecolor');
  const [date, setDate] = useState(() => isoDay(1));
  const [globe, setGlobe] = useState(true);
  const [terrain, setTerrain] = useState(true);
  const [exaggeration, setExaggeration] = useState(2);
  const [scale, setScale] = useState(1.5);
  const [pitch, setPitch] = useState(45);
  const [columns, setColumns] = useState(true);
  const [glow, setGlow] = useState(true);
  const [industrialOnly, setIndustrialOnly] = useState(false);
  const [showFacilities, setShowFacilities] = useState(false);
  const [showStates, setShowStates] = useState(true);
  const [liveFires, setLiveFires] = useState(false);
  const [spin, setSpin] = useState(false);

  const colors = useMemo(() => Object.fromEntries(classes.map(c => [c.id, c.color])), [classes]);
  const stats = useMemo(() => globeStats(thermal), [thermal]);
  const geo = useMemo(() => toGlobeGeoJSON(thermal, colors, scale), [thermal, colors, scale]);

  // ── create the map once ──────────────────────────────────────────────────
  useEffect(() => {
    if (!box.current) return;
    let map: maplibregl.Map;
    try {
      const v = GLOBE_VIEWS.india;
      const vis = (on: boolean) => ({ visibility: on ? 'visible' : 'none' }) as const;
      map = new maplibregl.Map({
        container: box.current,
        attributionControl: { compact: true, customAttribution: ATTR },
        center: v.center, zoom: v.zoom, pitch: v.pitch, bearing: v.bearing, maxPitch: 85,
        style: {
          version: 8,
          projection: { type: 'globe' },
          sources: {
            bluemarble: { type: 'raster', tiles: [GIBS_BLUE_MARBLE], tileSize: 256, maxzoom: 8 },
            truecolor: { type: 'raster', tiles: [gibsTrueColor(isoDay(1))], tileSize: 256, maxzoom: 9 },
            night: { type: 'raster', tiles: [GIBS_NIGHT], tileSize: 256, maxzoom: 8 },
            streets: { type: 'raster', tiles: [streetsUrl()], tileSize: 256, maxzoom: 19 },
            fires: { type: 'raster', tiles: [gibsFires(isoDay(1))], tileSize: 256, maxzoom: 9 },
            dem: { type: 'raster-dem', tiles: [TERRAIN_TILES], tileSize: 256, maxzoom: 12, encoding: 'terrarium' },
          },
          layers: [
            { id: 'space', type: 'background', paint: { 'background-color': '#020617' } },
            { id: 'bluemarble', type: 'raster', source: 'bluemarble', layout: vis(false) },
            { id: 'truecolor', type: 'raster', source: 'truecolor', layout: vis(true) },
            { id: 'night', type: 'raster', source: 'night', layout: vis(false) },
            { id: 'streets', type: 'raster', source: 'streets', layout: vis(false) },
            { id: 'hillshade', type: 'hillshade', source: 'dem', paint: { 'hillshade-exaggeration': 0.35, 'hillshade-shadow-color': '#0f172a' } },
            { id: 'fires', type: 'raster', source: 'fires', layout: vis(false) },
          ],
        },
      });
    } catch (e) {
      setFailed(e instanceof Error ? e.message : 'WebGL is not available');
      return;
    }
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right');
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
    map.addControl(new maplibregl.FullscreenControl(), 'bottom-right');

    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, maxWidth: '260px' });

    map.on('load', () => {
      try {
        map.setSky({ 'sky-color': '#0b1d3a', 'horizon-color': '#38bdf8', 'fog-color': '#e0f2fe', 'sky-horizon-blend': 0.6, 'horizon-fog-blend': 0.4, 'fog-ground-blend': 0.2, 'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 1, 7, 0.3, 9, 0] } as never);
      } catch { /* older style spec: no atmosphere */ }

      map.addSource('states', { type: 'geojson', data: EMPTY as never });
      map.addSource('facilities', { type: 'geojson', data: EMPTY as never });
      map.addSource('t-points', { type: 'geojson', data: EMPTY as never });
      map.addSource('t-columns', { type: 'geojson', data: EMPTY as never });

      map.addLayer({ id: 'states-line', type: 'line', source: 'states', paint: { 'line-color': '#f8fafc', 'line-width': 0.8, 'line-opacity': 0.7 } });
      map.addLayer({
        id: 'facilities', type: 'circle', source: 'facilities', layout: { visibility: 'none' },
        paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, 1.5, 8, 4], 'circle-color': '#38bdf8', 'circle-stroke-color': '#fff', 'circle-stroke-width': 0.6, 'circle-opacity': 0.85 },
      });
      map.addLayer({
        id: 'glow', type: 'circle', source: 't-points',
        layout: { 'circle-sort-key': ['get', 'ind'] },
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, 3, 5, 5, 9, 8, 12, 14],
          'circle-color': ['get', 'color'], 'circle-opacity': 0.85, 'circle-blur': 0.25, 'circle-stroke-color': '#fff', 'circle-stroke-width': 0.7,
        },
      });
      map.addLayer({
        id: 'columns', type: 'fill-extrusion', source: 't-columns',
        paint: { 'fill-extrusion-color': ['get', 'color'], 'fill-extrusion-height': ['get', 'h'], 'fill-extrusion-base': 0, 'fill-extrusion-opacity': 0.92, 'fill-extrusion-vertical-gradient': true },
      });
      map.addLayer({
        id: 'selected', type: 'circle', source: 't-points', filter: ['==', ['get', 'id'], ''],
        paint: { 'circle-radius': 16, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#fde047', 'circle-stroke-width': 3 },
      });

      for (const id of ['glow', 'columns']) {
        map.on('click', id, e => { const f = e.features?.[0]; if (f) onSelectRef.current(String(f.properties?.id)); });
        map.on('mouseenter', id, () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', id, () => { map.getCanvas().style.cursor = ''; popup.remove(); });
        map.on('mousemove', id, e => {
          const f = e.features?.[0];
          if (!f) return;
          const p = f.properties as Record<string, string | number>;
          const el = document.createElement('div');
          el.style.cssText = 'font:13px/1.4 "IBM Plex Sans",sans-serif;color:#0f172a';
          const b = document.createElement('b');
          b.textContent = String(p.site || p.label);
          const s = document.createElement('div');
          s.textContent = `${p.label} · ${p.frp} MW${p.alert ? ' · alert: ' + p.alert : ''}`;
          el.append(b, s);
          popup.setLngLat(e.lngLat).setDOMContent(el).addTo(map);
        });
      }
      setReady(true);
    });

    map.on('pitchend', () => setPitch(Math.round(map.getPitch())));
    map.on('dragstart', () => setSpin(false));
    return () => { popup.remove(); map.remove(); mapRef.current = null; setReady(false); };
  }, []);

  const src = (id: string) => mapRef.current?.getSource(id) as maplibregl.GeoJSONSource | undefined;

  // ── data ─────────────────────────────────────────────────────────────────
  useEffect(() => { if (ready) { src('t-points')?.setData(geo.points as never); src('t-columns')?.setData(geo.columns as never); } }, [ready, geo]);
  useEffect(() => { if (ready) src('facilities')?.setData((facilities ?? EMPTY) as never); }, [ready, facilities]);
  useEffect(() => { if (ready) src('states')?.setData((states ?? EMPTY) as never); }, [ready, states]);

  // ── visibility, filters, projection, terrain ─────────────────────────────
  useEffect(() => {
    const m = mapRef.current;
    if (!ready || !m) return;
    const set = (id: string, on: boolean) => m.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
    for (const b of BASES) set(b.id, b.id === base);
    set('fires', liveFires);
    set('facilities', showFacilities);
    set('states-line', showStates);
    set('columns', columns);
    set('glow', glow);
    set('hillshade', terrain);
    const f = industrialOnly ? ['==', ['get', 'ind'], 1] : null;
    m.setFilter('glow', f as never);
    m.setFilter('columns', f as never);
  }, [ready, base, liveFires, showFacilities, showStates, columns, glow, terrain, industrialOnly]);

  useEffect(() => { if (ready) mapRef.current?.setProjection({ type: globe ? 'globe' : 'mercator' }); }, [ready, globe]);
  useEffect(() => {
    if (ready) mapRef.current?.setTerrain(terrain ? { source: 'dem', exaggeration } : null);
  }, [ready, terrain, exaggeration]);
  useEffect(() => {
    const m = mapRef.current;
    if (!ready || !m) return;
    (m.getSource('truecolor') as maplibregl.RasterTileSource | undefined)?.setTiles([gibsTrueColor(date)]);
    (m.getSource('fires') as maplibregl.RasterTileSource | undefined)?.setTiles([gibsFires(date)]);
  }, [ready, date]);

  // ── selection: ring + fly ────────────────────────────────────────────────
  useEffect(() => {
    const m = mapRef.current;
    if (!ready || !m) return;
    m.setFilter('selected', ['==', ['get', 'id'], selectedId ?? '']);
    if (!selectedId) return;
    const hit = thermal?.features.find(f => f.properties.source_id === selectedId);
    const g = hit?.geometry as { coordinates?: [number, number] } | undefined;
    if (g?.coordinates) m.flyTo({ center: g.coordinates, zoom: Math.max(m.getZoom(), 10), pitch: Math.max(m.getPitch(), 55), duration: 2200 });
    // fly only when the selection changes, not on every data refresh
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, selectedId]);

  // ── slow auto-rotate while zoomed out ────────────────────────────────────
  useEffect(() => {
    const m = mapRef.current;
    if (!ready || !m || !spin) return;
    let stopped = false;
    const step = () => {
      if (stopped || m.getZoom() > 5) return;
      m.easeTo({ center: [m.getCenter().lng + 25, m.getCenter().lat], duration: 4000, easing: t => t });
    };
    m.on('moveend', step);
    step();
    return () => { stopped = true; m.off('moveend', step); m.stop(); };
  }, [ready, spin]);

  const fly = (key: string) => {
    const v = GLOBE_VIEWS[key];
    setSpin(false);
    mapRef.current?.flyTo({ center: v.center, zoom: v.zoom, pitch: v.pitch, bearing: v.bearing, duration: 2500 });
  };
  const setTilt = (p: number) => { setPitch(p); mapRef.current?.easeTo({ pitch: p, duration: 300 }); };

  if (failed) {
    return (
      <div role="alert" style={{ padding: 24, color: '#991b1b' }}>
        The 3D globe needs WebGL, which this browser or device could not start ({failed}). The flat Map tab still works.
      </div>
    );
  }

  const chkRow = (label: string, on: boolean, set: (v: boolean) => void, id: string) => (
    <label key={id} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '3px 0', cursor: 'pointer' }}>
      <input type="checkbox" checked={on} onChange={e => set(e.target.checked)} data-testid={id} /> {label}
    </label>
  );

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', background: '#020617' }}>
      <div ref={box} data-testid="globe-canvas" style={{ position: 'absolute', inset: 0 }} />

      {/* controls */}
      <div style={{ ...panel, top: 12, left: 12, width: 260, maxWidth: 'calc(100% - 24px)', maxHeight: 'calc(100% - 24px)', overflowY: 'auto' }}>
        <button onClick={() => setOpen(o => !o)} aria-expanded={open}
          style={{ width: '100%', textAlign: 'left', padding: '10px 14px', border: 'none', background: 'none', color: '#fff', fontWeight: 700, fontSize: 14, cursor: 'pointer' }}>
          3D Earth {open ? '▾' : '▸'}
        </button>
        {open && (
          <div style={{ padding: '0 14px 14px' }}>
            <p style={h}>Imagery</p>
            <div role="radiogroup" aria-label="Imagery" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {BASES.map(b => (
                <button key={b.id} role="radio" aria-checked={base === b.id} title={b.note} onClick={() => setBase(b.id)} style={chip(base === b.id)}>{b.label}</button>
              ))}
            </div>
            <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
              Day
              <input type="date" value={date} max={isoDay(0)} min="2012-01-20" onChange={e => e.target.value && setDate(e.target.value)}
                style={{ background: '#1e293b', color: '#fff', border: '1px solid #475569', borderRadius: 8, padding: '3px 6px' }} />
            </label>

            <p style={h}>Fire data</p>
            {chkRow('3D columns (height = power)', columns, setColumns, 'g-columns')}
            {chkRow('Glow points', glow, setGlow, 'g-glow')}
            {chkRow('Industrial only', industrialOnly, setIndustrialOnly, 'g-industrial')}
            {chkRow('NASA fire pixels (global, that day)', liveFires, setLiveFires, 'g-fires')}
            {chkRow('Facilities', showFacilities, setShowFacilities, 'g-facilities')}
            {chkRow('State borders', showStates, setShowStates, 'g-states')}

            <p style={h}>Shape</p>
            {chkRow('Globe (off = flat map)', globe, setGlobe, 'g-globe')}
            {chkRow('Terrain relief', terrain, setTerrain, 'g-terrain')}
            {chkRow('Auto-rotate', spin, setSpin, 'g-spin')}
            <label style={{ display: 'block', marginTop: 6 }}>Terrain exaggeration ×{exaggeration}
              <input type="range" min={1} max={5} step={0.5} value={exaggeration} onChange={e => setExaggeration(Number(e.target.value))} style={{ width: '100%' }} />
            </label>
            <label style={{ display: 'block' }}>Column height ×{scale}
              <input type="range" min={0.5} max={4} step={0.5} value={scale} onChange={e => setScale(Number(e.target.value))} style={{ width: '100%' }} />
            </label>
            <label style={{ display: 'block' }}>Tilt {pitch}°
              <input type="range" min={0} max={80} step={5} value={pitch} onChange={e => setTilt(Number(e.target.value))} style={{ width: '100%' }} />
            </label>

            <p style={h}>Fly to</p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {Object.entries(GLOBE_VIEWS).map(([k, v]) => <button key={k} onClick={() => fly(k)} style={chip(false)}>{v.label}</button>)}
              <button onClick={() => mapRef.current?.easeTo({ bearing: 0, pitch: 0, duration: 600 })} style={chip(false)}>North up</button>
            </div>
          </div>
        )}
      </div>

      {/* legend + numbers */}
      <div style={{ ...panel, top: 12, right: 12, padding: '10px 14px', maxWidth: 230 }} data-testid="globe-legend">
        <p style={{ margin: 0, fontWeight: 700 }}>
          {stats.total.toLocaleString()} sources · {stats.industrial.toLocaleString()} industrial
        </p>
        <p style={{ margin: '2px 0 6px', color: '#cbd5e1' }}>
          {stats.alerts ? `${stats.alerts} with alerts · ` : ''}peak {Math.round(stats.maxFrp)} MW
        </p>
        {classes.map(c => (
          <div key={c.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '1px 0' }}>
            <span style={{ width: 11, height: 11, borderRadius: 3, background: c.color, flexShrink: 0 }} />
            <span>{c.label}</span>
          </div>
        ))}
        <p style={{ margin: '8px 0 0', fontSize: 12, color: '#94a3b8' }}>Column height grows with fire power (FRP). Click a column for details.</p>
      </div>

      <p style={{ position: 'absolute', zIndex: 5, left: '50%', transform: 'translateX(-50%)', bottom: 34, margin: 0, padding: '4px 12px', borderRadius: 99,
        background: 'rgba(15,23,42,0.7)', color: '#cbd5e1', fontSize: 12, whiteSpace: 'nowrap' }} className="hidden-mobile">
        Drag to rotate · scroll to zoom · right-drag or Ctrl-drag to tilt
      </p>
    </div>
  );
}
