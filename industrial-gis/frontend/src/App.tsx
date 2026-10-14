import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Header } from '@/components/Header';
import { StatisticsCards } from '@/components/StatisticsCards';
import { MapView, EVIDENCE_COLORS, type FitRequest, type MapViewport } from '@/components/MapView';
import { IndustryDetails } from '@/components/IndustryDetails';
import { AnalyticsPanel } from '@/components/AnalyticsPanel';
import { LoadingState } from '@/components/LoadingState';
import { ErrorState } from '@/components/ErrorState';
import { LiveStatus, type UiUpdateInfo } from '@/components/LiveStatus';
import { ExportMenu } from '@/components/ExportMenu';
import { ThermalControls } from '@/components/ThermalControls';
import { ThermalDetails } from '@/components/ThermalDetails';
import { useThermal } from '@/hooks/useThermal';
import {
  ApiError,
  checkHealth,
  fetchBoundaries,
  fetchFilterOptions,
  fetchFacilities,
  fetchIndustrialSite,
  fetchIndustries,
  fetchStatistics,
  searchIndustries,
} from '@/services/api';
import { useDebouncedCallback } from '@/hooks/useDebounce';
import { useLiveUpdates } from '@/hooks/useLiveUpdates';
import { useSyncStatus } from '@/hooks/useSyncStatus';
import { geometryBounds, INDIA_BOUNDS, mergeBounds } from '@/utils/geo';
import type {
  BoundaryFeature,
  FeatureCollection,
  FilterOptionsResponse,
  HealthResponse,
  FacilityCollection,
  IndustrialSiteFeature,
  RevisionEvent,
  StatisticsResponse,
} from '@/types/industrial';

const SITE_LIMIT = 2000;          // max markers requested for the current viewport
const LIVE_DEBOUNCE_MS = 1500;    // batch bursts of change notifications into one refresh
const DISTRICT_MIN_ZOOM = 7;      // district outlines are only fetched when zoomed in (or a state is chosen)

export default function App() {
  // ── Filter state ──────────────────────────────────────────────────────────
  const [searchDraft, setSearchDraft] = useState('');
  const [activeQuery, setActiveQuery] = useState('');
  const [selectedState, setSelectedState] = useState('');
  const [selectedDistrict, setSelectedDistrict] = useState('');
  const [selectedIndustryType, setSelectedIndustryType] = useState('');
  const [selectedStatus, setSelectedStatus] = useState('');
  const [selectedConfidence, setSelectedConfidence] = useState('');
  const [viewport, setViewport] = useState<MapViewport | null>(null);
  // the OSM site layer is a North-East pilot; the thermal layer is the default view
  const [showSites, setShowSites] = useState(false);
  const [showFacilities, setShowFacilities] = useState(true);
  const [facilities, setFacilities] = useState<FacilityCollection | null>(null);

  // ── Data state ────────────────────────────────────────────────────────────
  const [options, setOptions] = useState<FilterOptionsResponse | null>(null);
  const [statistics, setStatistics] = useState<StatisticsResponse | null>(null);
  const [sites, setSites] = useState<FeatureCollection<IndustrialSiteFeature> | null>(null);
  const [selectedSiteId, setSelectedSiteId] = useState<string | null>(null);
  const [selectedSite, setSelectedSite] = useState<IndustrialSiteFeature | null>(null);
  const [selectedRemoved, setSelectedRemoved] = useState(false);
  const [stateBoundaries, setStateBoundaries] = useState<FeatureCollection<BoundaryFeature> | null>(null);
  const [districtBoundaries, setDistrictBoundaries] = useState<FeatureCollection<BoundaryFeature> | null>(null);
  const [showStates, setShowStates] = useState(true);
  const [showDistricts, setShowDistricts] = useState(false);
  const [fitRequest, setFitRequest] = useState<FitRequest | null>(null);

  // ── UI state ──────────────────────────────────────────────────────────────
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [showAnalytics, setShowAnalytics] = useState(false);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [backendStatus, setBackendStatus] = useState<'checking' | 'online' | 'offline'>('checking');
  const [lastUiUpdate, setLastUiUpdate] = useState<UiUpdateInfo | null>(null);
  const [liveToast, setLiveToast] = useState<string | null>(null);

  // Live refresh: bump `refreshToken` to refetch everything visible with the SAME filters/viewport.
  const [refreshToken, setRefreshToken] = useState(0);
  const pendingLive = useRef<{ revision: number; committedAtMs: number | null; token: number } | null>(null);

  const { status: syncStatus, unreachable: syncUnreachable, refresh: refreshSyncStatus } = useSyncStatus();

  // ── Live updates (SSE, polling fallback) ──────────────────────────────────
  const liveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleRevision = useCallback((event: RevisionEvent) => {
    const committed = event.committed_at ? Date.parse(event.committed_at) : NaN;
    pendingLive.current = {
      revision: event.revision,
      committedAtMs: Number.isNaN(committed) ? null : committed,
      token: -1,
    };
    if (liveTimer.current) clearTimeout(liveTimer.current);
    liveTimer.current = setTimeout(() => {
      setRefreshToken(t => {
        if (pendingLive.current) pendingLive.current.token = t + 1;
        return t + 1;
      });
      void refreshSyncStatus();
    }, LIVE_DEBOUNCE_MS);
  }, [refreshSyncStatus]);
  useEffect(() => () => { if (liveTimer.current) clearTimeout(liveTimer.current); }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchFacilities(controller.signal).then(setFacilities).catch(() => { /* layer simply stays empty */ });
    return () => controller.abort();
  }, []);

  const thermal = useThermal();
  const refreshThermal = thermal.refresh;
  // a new FIRMS snapshot was published: refetch thermal data only (sites are unaffected)
  const handleThermalEvent = useCallback(() => {
    refreshThermal();
    setLiveToast('Thermal data updated');
  }, [refreshThermal]);

  const { connection } = useLiveUpdates({
    onRevision: handleRevision, onSyncEvent: refreshSyncStatus, onThermal: handleThermalEvent,
  });
  const thermalColors = useMemo(() => Object.fromEntries(thermal.classes.map(c => [c.id, c.color])), [thermal.classes]);

  // ── Debounced inputs ──────────────────────────────────────────────────────
  const debouncedCommitSearch = useDebouncedCallback((q: string) => setActiveQuery(q.trim()), 420);
  const debouncedViewport = useDebouncedCallback((v: MapViewport) => setViewport(v), 500);

  const handleSearchDraftChange = useCallback((value: string) => {
    setSearchDraft(value);
    if (value.length === 0) debouncedCommitSearch('');
    else if (value.length >= 2) debouncedCommitSearch(value);
  }, [debouncedCommitSearch]);

  // ── Health probe (liveness + database readiness), every 10 s ─────────────
  const probeHealth = useCallback(() => {
    checkHealth()
      .then(h => { setHealth(h); setBackendStatus('online'); })
      .catch(() => setBackendStatus('offline'));
  }, []);
  useEffect(() => {
    probeHealth();
    const id = setInterval(probeHealth, 10_000);
    return () => clearInterval(id);
  }, [probeHealth]);

  // ── Query objects ─────────────────────────────────────────────────────────
  /** Attribute filters only: defines the SCOPE of statistics and exports. */
  const scopeFilters = useMemo(() => ({
    state: selectedState || undefined,
    district: selectedDistrict || undefined,
    industry_type: selectedIndustryType || undefined,
    status: selectedStatus || undefined,
    confidence: selectedConfidence || undefined,
    q: activeQuery || undefined,
  }), [selectedState, selectedDistrict, selectedIndustryType, selectedStatus, selectedConfidence, activeQuery]);

  // ── Filter options (districts cascade from the chosen state) ─────────────
  const optionsSeq = useRef(0);
  useEffect(() => {
    const mine = ++optionsSeq.current;
    const controller = new AbortController();
    fetchFilterOptions(selectedState || undefined, controller.signal)
      .then(opts => { if (mine === optionsSeq.current) setOptions(opts); })
      .catch(() => { /* surfaced through health/site errors */ });
    return () => controller.abort();
  }, [selectedState, refreshToken]);

  // ── Statistics: scope = attribute filters over the whole dataset ─────────
  const statsSeq = useRef(0);
  useEffect(() => {
    const mine = ++statsSeq.current;
    const controller = new AbortController();
    fetchStatistics(scopeFilters, controller.signal)
      .then(stats => { if (mine === statsSeq.current) setStatistics(stats); })
      .catch(() => { /* keep previous numbers; the error banner covers outages */ });
    return () => controller.abort();
  }, [scopeFilters, refreshToken]);

  // ── Sites: scope filters + current map extent ────────────────────────────
  const sitesSeq = useRef(0);
  useEffect(() => {
    if (!viewport) return; // wait for the first map extent: never request the whole dataset
    const mine = ++sitesSeq.current;
    const controller = new AbortController();
    const isLiveRefresh = pendingLive.current?.token === refreshToken && refreshToken > 0;
    if (!isLiveRefresh) setLoading(true);
    setError(null);

    const { q, ...filters } = scopeFilters;
    const params = { ...filters, bbox: viewport.bbox, limit: SITE_LIMIT };
    const request = q
      ? searchIndustries(q, params, controller.signal)
      : fetchIndustries(params, controller.signal);

    request
      .then(data => {
        if (mine !== sitesSeq.current) return; // a newer request superseded this one
        setSites(data);
        setLoading(false);
        setBackendStatus('online');
        const live = pendingLive.current;
        if (live && live.token === refreshToken && refreshToken > 0) {
          setLastUiUpdate({
            revision: live.revision,
            commitToUiSeconds: live.committedAtMs ? Math.max((Date.now() - live.committedAtMs) / 1000, 0) : null,
            atMs: Date.now(),
          });
          setLiveToast(`Data updated (revision ${live.revision})`);
          pendingLive.current = null;
        }
      })
      .catch((err: unknown) => {
        if (mine !== sitesSeq.current) return;
        if (err instanceof ApiError && err.aborted) return;
        const apiError = err instanceof ApiError ? err : new ApiError(0, String(err), '/industries');
        setError(apiError);
        setLoading(false);
        if (apiError.isNetworkError) setBackendStatus('offline');
      });
    return () => controller.abort();
  }, [scopeFilters, viewport, refreshToken]);

  useEffect(() => {
    if (!liveToast) return;
    const id = setTimeout(() => setLiveToast(null), 4000);
    return () => clearTimeout(id);
  }, [liveToast]);

  // ── Selected site: refetch on selection AND after every live refresh ──────
  const detailSeq = useRef(0);
  useEffect(() => {
    if (!selectedSiteId) return;
    const mine = ++detailSeq.current;
    const controller = new AbortController();
    fetchIndustrialSite(selectedSiteId, controller.signal)
      .then(feature => {
        if (mine !== detailSeq.current) return;
        setSelectedSite(feature);
        setSelectedRemoved(false);
      })
      .catch((err: unknown) => {
        if (mine !== detailSeq.current) return;
        if (err instanceof ApiError && err.status === 404) setSelectedRemoved(true);
      });
    return () => controller.abort();
  }, [selectedSiteId, refreshToken]);

  // ── Boundaries ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!showStates) { setStateBoundaries(null); return; }
    const controller = new AbortController();
    fetchBoundaries('states', {}, controller.signal)
      .then(setStateBoundaries)
      .catch(() => setStateBoundaries(null));
    return () => controller.abort();
  }, [showStates]);

  const districtViewKey = selectedState ? `state:${selectedState}` : (viewport && viewport.zoom >= DISTRICT_MIN_ZOOM ? `bbox:${viewport.bbox}` : '');
  useEffect(() => {
    if (!showDistricts || !districtViewKey) { setDistrictBoundaries(null); return; }
    const controller = new AbortController();
    const params = selectedState ? { state: selectedState } : { bbox: viewport?.bbox };
    fetchBoundaries('districts', params, controller.signal)
      .then(setDistrictBoundaries)
      .catch(() => setDistrictBoundaries(null));
    return () => controller.abort();
    // viewport is represented by districtViewKey
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showDistricts, districtViewKey]);

  // ── Fit map to the chosen state / district (explicit user action only) ───
  const fitCounter = useRef(0);
  const requestFit = useCallback((bounds: [number, number, number, number]) => {
    fitCounter.current += 1;
    setFitRequest({ id: fitCounter.current, bounds });
  }, []);

  const fitToState = useCallback((state: string) => {
    fetchBoundaries('states', { state, simplify: 0.05 })
      .then(fc => {
        const box = mergeBounds(fc.features.map(f => geometryBounds(f.geometry)));
        if (box) requestFit(box);
      })
      .catch(() => { /* boundaries not loaded: map simply stays where it is */ });
  }, [requestFit]);

  const fitToDistrict = useCallback((state: string, district: string) => {
    fetchBoundaries('districts', state ? { state, simplify: 0.02 } : { simplify: 0.02 })
      .then(fc => {
        const match = fc.features.filter(f => f.properties.name.toLowerCase() === district.toLowerCase());
        const box = mergeBounds(match.map(f => geometryBounds(f.geometry)));
        if (box) requestFit(box);
      })
      .catch(() => { /* ignore */ });
  }, [requestFit]);

  // ── Handlers ──────────────────────────────────────────────────────────────
  const handleSelectSite = useCallback((siteId: string) => {
    setSelectedRemoved(false);
    setSelectedSiteId(siteId);
    const cached = sites?.features.find(f => f.properties.site_id === siteId) ?? null;
    if (cached) setSelectedSite(cached); // instant; replaced by the full-geometry detail response
  }, [sites]);

  const handleStateChange = useCallback((state: string) => {
    setSelectedState(state);
    setSelectedDistrict('');
    if (state) fitToState(state);
  }, [fitToState]);

  const handleDistrictChange = useCallback((district: string) => {
    setSelectedDistrict(district);
    if (district) fitToDistrict(selectedState, district);
    else if (selectedState) fitToState(selectedState);
  }, [fitToDistrict, fitToState, selectedState]);

  const handleReset = useCallback(() => {
    setSearchDraft('');
    setActiveQuery('');
    setSelectedState('');
    setSelectedDistrict('');
    setSelectedIndustryType('');
    setSelectedStatus('');
    setSelectedConfidence('');
    setSelectedSiteId(null);
    setSelectedSite(null);
    setSelectedRemoved(false);
    requestFit(INDIA_BOUNDS);
  }, [requestFit]);

  const handleCloseDetails = useCallback(() => {
    setSelectedSiteId(null);
    setSelectedSite(null);
    setSelectedRemoved(false);
  }, []);

  const handleRetry = useCallback(() => {
    setBackendStatus('checking');
    probeHealth();
    setRefreshToken(t => t + 1);
    void refreshSyncStatus();
  }, [probeHealth, refreshSyncStatus]);

  // ── Derived display values ────────────────────────────────────────────────
  const visibleCount = sites?.features.length ?? 0;
  const viewTotal = sites?.meta?.total ?? visibleCount;
  const truncated = sites?.meta?.truncated ?? false;
  const isRefreshing = loading && sites !== null;
  const isInitialLoad = loading && sites === null;
  const synthetic = health?.synthetic === true || sites?.meta?.synthetic === true;
  const dbProblem = health !== null && !synthetic && health.database?.status !== undefined
    && health.database.status !== 'ok' && health.database.status !== 'not_used';
  const datasetEmpty = health !== null && !synthetic && health.ready === false && !dbProblem
    && (health.dataset?.active_sites ?? 0) === 0;
  const anyFilter = Boolean(activeQuery || selectedState || selectedDistrict || selectedIndustryType
    || selectedStatus || selectedConfidence);
  const dataMode = health?.data_mode ?? '';

  return (
    <div
      style={{
        display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden',
        background: 'radial-gradient(ellipse at top left, rgba(14,165,233,0.09) 0%, transparent 50%), linear-gradient(180deg, #f0f7ff 0%, #e8f0fe 100%)',
        fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif",
      }}
    >
      {backendStatus === 'offline' && (
        <Banner tone="amber" onRetry={handleRetry}>
          API unreachable - showing the last data received. Filters, search and live updates resume when it reconnects.
        </Banner>
      )}
      {backendStatus !== 'offline' && dbProblem && (
        <Banner tone="red" onRetry={handleRetry}>
          The API is running but the database is unavailable. Data on screen may be stale.
        </Banner>
      )}
      {synthetic && (
        <Banner tone="violet">
          DEMO MODE - these ~30 sites are synthetic placeholders for offline testing, not real facilities. Start the stack with DATA_MODE=postgis for real data.
        </Banner>
      )}

      <div style={{ flexShrink: 0, padding: '12px 14px 0' }}>
        <Header
          searchDraft={searchDraft}
          selectedState={selectedState}
          selectedDistrict={selectedDistrict}
          selectedIndustryType={selectedIndustryType}
          selectedStatus={selectedStatus}
          selectedConfidence={selectedConfidence}
          options={options}
          availableDistricts={options?.districts}
          isLoading={isRefreshing}
          onSearchDraftChange={handleSearchDraftChange}
          onStateChange={handleStateChange}
          onDistrictChange={handleDistrictChange}
          onIndustryTypeChange={setSelectedIndustryType}
          onStatusChange={setSelectedStatus}
          onConfidenceChange={setSelectedConfidence}
          onSearch={() => setActiveQuery(searchDraft.trim())}
          onReset={handleReset}
        />
      </div>

      <div style={{ flexShrink: 0, padding: '10px 14px 0' }}>
        <StatisticsCards statistics={statistics} onShowAnalytics={() => setShowAnalytics(true)} />
        <p style={{ margin: '4px 4px 0', fontSize: 10, color: '#94a3b8' }}>
          Cards and analytics count{' '}
          <strong>{anyFilter ? 'all sites matching your filters' : 'the whole loaded dataset'}</strong>
          {' '}(not just the map view).
        </p>
      </div>

      {/* ── Status bar ──────────────────────────────────────────────────── */}
      <div style={{ flexShrink: 0, padding: '6px 18px', display: 'flex', alignItems: 'center', gap: 10, minHeight: 32, flexWrap: 'wrap' }}>
        {isRefreshing && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#0ea5e9', fontWeight: 500 }}>
            <span style={{ width: 12, height: 12, borderRadius: '50%', border: '2px solid #bae6fd', borderTopColor: '#0ea5e9', animation: 'spin 0.7s linear infinite', display: 'inline-block' }} />
            Refreshing…
          </span>
        )}
        {!isRefreshing && !error && sites && (
          <span id="visible-count" style={{ fontSize: 12, color: '#64748b' }} aria-live="polite">
            <strong style={{ color: '#0f172a' }}>{visibleCount.toLocaleString()}</strong>
            {truncated && <> of {viewTotal.toLocaleString()}</>} site{visibleCount !== 1 ? 's' : ''} in the map view
            {activeQuery && <> matching <em style={{ color: '#0ea5e9' }}>"{activeQuery}"</em></>}
            {truncated && <span style={{ color: '#b45309' }}> - showing the first {SITE_LIMIT.toLocaleString()}; zoom in or add filters to see the rest</span>}
          </span>
        )}
        {error && (
          <span role="alert" style={{ fontSize: 12, color: '#dc2626', fontWeight: 600 }}>
            ⚠ {error.isServerUnavailable ? 'Database unavailable' : error.message}
            <button onClick={handleRetry} style={{ marginLeft: 8, fontSize: 11, cursor: 'pointer' }}>Retry</button>
          </span>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <ExportMenu
            filters={scopeFilters}
            viewportBbox={viewport?.bbox ?? null}
            totalMatching={statistics?.total_sites ?? null}
            disabled={backendStatus === 'offline' || synthetic}
          />
          <span style={{
            fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em',
            padding: '2px 10px', borderRadius: 99,
            background: dataMode === 'postgis' ? 'rgba(34,197,94,0.10)' : 'rgba(139,92,246,0.10)',
            color: dataMode === 'postgis' ? '#15803d' : '#6d28d9',
          }}>
            {dataMode === 'postgis' ? 'PostGIS · OpenStreetMap' : dataMode ? 'Synthetic demo' : '…'}
          </span>
          <LiveStatus
            status={syncStatus}
            unreachable={syncUnreachable || backendStatus === 'offline'}
            connection={connection}
            lastUiUpdate={lastUiUpdate}
            onRefresh={refreshSyncStatus}
          />
        </div>
      </div>

      {/* ── Map ─────────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, minHeight: 0, padding: '0 14px 14px' }}>
        <div style={{
          width: '100%', height: '100%', borderRadius: 20, overflow: 'hidden', position: 'relative',
          boxShadow: '0 8px 40px rgba(15,23,42,0.12), 0 2px 8px rgba(15,23,42,0.06)',
          border: '1px solid rgba(255,255,255,0.8)', background: '#f0f4f8',
        }}>
          {isInitialLoad && !error && (
            <div style={{ position: 'absolute', inset: 0, zIndex: 10, background: 'rgba(240,244,248,0.92)', backdropFilter: 'blur(4px)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <LoadingState message="Loading industrial sites…" />
            </div>
          )}
          {error && !sites && (
            <div style={{ position: 'absolute', inset: 0, zIndex: 10, background: 'rgba(240,244,248,0.95)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <ErrorState
                message={error.isServerUnavailable ? 'The database is unavailable. Check that PostGIS is running.' : error.message}
                onRetry={handleRetry}
              />
            </div>
          )}

          <MapView
            sites={sites}
            showSites={showSites}
            facilities={showFacilities ? facilities : null}
            selectedSiteId={selectedSiteId}
            selectedSite={selectedSite}
            stateBoundaries={stateBoundaries}
            districtBoundaries={districtBoundaries}
            fitRequest={fitRequest}
            onSelectSite={handleSelectSite}
            onViewportChange={debouncedViewport}
            thermal={thermal.enabled ? thermal.data : null}
            thermalColors={thermalColors}
            selectedThermalId={thermal.selectedId}
            onSelectThermal={thermal.select}
          />
          <ThermalControls t={thermal} />

          {/* Layer toggles */}
          <div style={{
            position: 'absolute', top: 12, right: 12, zIndex: 500, background: 'rgba(255,255,255,0.94)',
            borderRadius: 12, padding: '8px 12px', fontSize: 11, boxShadow: '0 2px 12px rgba(15,23,42,0.12)',
          }}>
            <p style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#94a3b8', margin: '0 0 4px' }}>Layers</p>
            <label style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}
              title="Merged OSM + WRI + Global Energy Monitor + VIIRS Nightfire facilities (blue = confirmed by 2+ sources)">
              <input id="toggle-facilities" type="checkbox" checked={showFacilities} onChange={e => setShowFacilities(e.target.checked)} /> Facilities ({facilities?.meta.total ?? 0})
            </label>
            {showFacilities && facilities?.meta.sources.length ? (
              <p style={{ fontSize: 9, color: '#64748b', margin: '2px 0 4px', maxWidth: 200, lineHeight: 1.35 }}>
                {facilities.meta.sources.map(s => s.attribution).join('; ')}
              </p>
            ) : null}
            <label style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}
              title="OpenStreetMap industrial sites (North-East pilot extract, not national coverage)">
              <input id="toggle-sites" type="checkbox" checked={showSites} onChange={e => setShowSites(e.target.checked)} /> Industrial sites (OSM pilot)
            </label>
            <p style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#94a3b8', margin: '6px 0 4px' }}>Boundaries</p>
            <label style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
              <input id="toggle-states" type="checkbox" checked={showStates} onChange={e => setShowStates(e.target.checked)} /> States
            </label>
            <label style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}
              title={`Districts load when zoomed in (zoom ≥ ${DISTRICT_MIN_ZOOM}) or when a state is selected`}>
              <input id="toggle-districts" type="checkbox" checked={showDistricts} onChange={e => setShowDistricts(e.target.checked)} /> Districts
            </label>
          </div>

          {/* Legend (site markers only) */}
          {showSites && <div style={{
            position: 'absolute', bottom: 16, left: 16, zIndex: 500, background: 'rgba(255,255,255,0.93)',
            backdropFilter: 'blur(10px)', borderRadius: 12, padding: '8px 12px', fontSize: 11,
            boxShadow: '0 2px 12px rgba(15,23,42,0.12)', maxWidth: 190,
          }}>
            <p style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#94a3b8', margin: '0 0 6px' }}>
              Evidence (tag completeness)
            </p>
            {(['high', 'medium', 'low'] as const).map(level => (
              <div key={level} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                <div style={{ width: 10, height: 10, borderRadius: '50% 50% 50% 0', transform: 'rotate(-45deg)', background: EVIDENCE_COLORS[level], flexShrink: 0 }} />
                <span style={{ color: '#374151', fontWeight: 500, textTransform: 'capitalize' }}>{level}</span>
              </div>
            ))}
            <p style={{ fontSize: 9, color: '#94a3b8', margin: '4px 0 0', lineHeight: 1.35 }}>
              Calculated from OSM tags. Not government verification.
            </p>
          </div>}

          {liveToast && (
            <div role="status" style={{
              position: 'absolute', top: 12, left: '50%', transform: 'translateX(-50%)', zIndex: 600,
              background: '#0f172a', color: '#fff', padding: '6px 14px', borderRadius: 99, fontSize: 12, fontWeight: 600,
            }}>
              {liveToast}
            </div>
          )}

          {!isInitialLoad && !error && sites && sites.features.length === 0 && (
            <div style={{
              position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', zIndex: 500,
              textAlign: 'center', background: 'rgba(255,255,255,0.95)', borderRadius: 16, padding: '24px 32px',
              boxShadow: '0 4px 24px rgba(15,23,42,0.12)', border: '1px solid #e2e8f0', maxWidth: 320,
            }}>
              <span style={{ fontSize: 36 }}>{datasetEmpty ? '📥' : '🔍'}</span>
              <p style={{ fontSize: 14, fontWeight: 700, color: '#1e293b', margin: '8px 0 4px' }}>
                {datasetEmpty ? 'No data imported yet' : 'No sites in this view'}
              </p>
              <p style={{ fontSize: 12, color: '#64748b', margin: '0 0 12px' }}>
                {datasetEmpty
                  ? 'The database is empty. Run the one-time bootstrap import (see README), then this page fills in automatically.'
                  : 'Pan or zoom the map, adjust the filters, or reset them.'}
              </p>
              {!datasetEmpty && (
                <button onClick={handleReset} style={{ padding: '7px 18px', borderRadius: 9, border: 'none', background: '#0f172a', color: '#fff', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>
                  Reset filters &amp; view
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      <IndustryDetails site={selectedSite} removed={selectedRemoved} onClose={handleCloseDetails} />
      <ThermalDetails
        feature={thermal.selected}
        info={thermal.classes.find(c => c.id === thermal.selected?.properties.thermal_class)}
        gone={thermal.selectedGone}
        onClose={() => thermal.select(null)}
      />
      <AnalyticsPanel statistics={statistics} isOpen={showAnalytics} onClose={() => setShowAnalytics(false)} />

      {(selectedSite || showAnalytics) && (
        <div
          onClick={() => { handleCloseDetails(); setShowAnalytics(false); }}
          style={{ position: 'fixed', inset: 0, zIndex: 550, background: 'rgba(15,23,42,0.3)', display: 'none' }}
          className="mobile-backdrop"
        />
      )}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
        @media (max-width: 640px) { .mobile-backdrop { display: block !important; } }
      `}</style>
    </div>
  );
}

function Banner({ tone, children, onRetry }: { tone: 'amber' | 'red' | 'violet'; children: React.ReactNode; onRetry?: () => void }) {
  const palette = {
    amber: { bg: '#fffbeb', border: '#fcd34d', fg: '#92400e' },
    red: { bg: '#fef2f2', border: '#fecaca', fg: '#991b1b' },
    violet: { bg: '#f5f3ff', border: '#ddd6fe', fg: '#5b21b6' },
  }[tone];
  return (
    <div role="alert" style={{
      flexShrink: 0, background: palette.bg, borderBottom: `1px solid ${palette.border}`, padding: '6px 18px',
      display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: palette.fg, fontWeight: 500,
    }}>
      <span style={{ flex: 1 }}>{children}</span>
      {onRetry && (
        <button onClick={onRetry} style={{
          padding: '3px 12px', borderRadius: 8, border: `1px solid ${palette.border}`, background: '#fff',
          color: palette.fg, fontSize: 11, fontWeight: 600, cursor: 'pointer',
        }}>
          Retry
        </button>
      )}
    </div>
  );
}
