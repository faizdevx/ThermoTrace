import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Header } from '@/components/Header';
import { StatisticsCards } from '@/components/StatisticsCards';
import { MapView } from '@/components/MapView';
import { IndustryDetails } from '@/components/IndustryDetails';
import { AnalyticsPanel } from '@/components/AnalyticsPanel';
import { LoadingState } from '@/components/LoadingState';
import { ErrorState } from '@/components/ErrorState';
import {
  checkHealth,
  fetchFilterOptions,
  fetchIndustrialSite,
  fetchIndustries,
  fetchStatistics,
  searchIndustries,
  ApiError,
} from '@/services/api';

import { useDebouncedCallback } from '@/hooks/useDebounce';
import type {
  FeatureCollection,
  FilterOptionsResponse,
  IndustrialSiteFeature,
  StatisticsResponse,
} from '@/types/industrial';

export default function App() {
  // ── Filter state ──────────────────────────────────────────────────────────
  const [searchDraft, setSearchDraft] = useState('');
  const [activeQuery, setActiveQuery] = useState('');
  const [selectedState, setSelectedState] = useState('');
  const [selectedDistrict, setSelectedDistrict] = useState('');
  const [selectedIndustryType, setSelectedIndustryType] = useState('');
  const [selectedStatus, setSelectedStatus] = useState('');
  const [currentBbox, setCurrentBbox] = useState<string | null>(null);

  // ── Data state ────────────────────────────────────────────────────────────
  const [options, setOptions] = useState<FilterOptionsResponse | null>(null);
  const [statistics, setStatistics] = useState<StatisticsResponse | null>(null);
  const [sites, setSites] = useState<FeatureCollection<IndustrialSiteFeature> | null>(null);
  const [selectedSiteId, setSelectedSiteId] = useState<string | null>(null);
  const [selectedSite, setSelectedSite] = useState<IndustrialSiteFeature | null>(null);

  // ── UI state ──────────────────────────────────────────────────────────────
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAnalytics, setShowAnalytics] = useState(false);
  // 'checking' → initial probe; 'online' → backend reachable; 'offline' → unreachable
  const [backendStatus, setBackendStatus] = useState<'checking' | 'online' | 'offline'>('checking');
  const [dataMode, setDataMode] = useState<string>('synthetic');

  // ── Debounced callbacks ───────────────────────────────────────────────────
  /** Search: auto-commit after 420ms of inactivity */
  const debouncedCommitSearch = useDebouncedCallback((q: string) => {
    setActiveQuery(q.trim());
  }, 420);

  /** Bbox: update from map after 650ms of inactivity */
  const handleBoundsChange = useDebouncedCallback((bbox: string) => {
    setCurrentBbox(bbox);
  }, 650);

  // ── Search draft handler: auto-search on typing, clear on empty ───────────
  const handleSearchDraftChange = useCallback((value: string) => {
    setSearchDraft(value);
    if (value.length === 0) {
      debouncedCommitSearch('');
    } else if (value.length >= 2) {
      debouncedCommitSearch(value);
    }
  }, [debouncedCommitSearch]);

  // ── Derived: available districts for selected state ───────────────────────
  // When a state is selected, districts are derived from the current filtered
  // site list (which the backend already filtered by state). This ensures
  // the district dropdown always shows only valid options for the current state.
  const availableDistricts = useMemo(() => {
    if (!selectedState) return options?.districts ?? [];
    if (!sites || sites.features.length === 0) return [];
    const districtSet = new Set(
      sites.features
        .filter(f => f.properties.state === selectedState)
        .map(f => f.properties.district),
    );
    return [...districtSet].sort();
  }, [selectedState, sites, options?.districts]);

  // ── Health probe (runs once on mount) ────────────────────────────────────
  useEffect(() => {
    checkHealth()
      .then(h => {
        setBackendStatus('online');
        setDataMode(h.data_mode);
      })
      .catch(() => setBackendStatus('offline'));
  }, []);

  // ── Bootstrap: fetch options + statistics on mount ────────────────────────
  useEffect(() => {
    Promise.all([fetchFilterOptions(), fetchStatistics()])
      .then(([opts, stats]) => {
        setOptions(opts);
        setStatistics(stats);
      })
      .catch(err => console.error('Bootstrap error:', err));
  }, []);

  // ── Fetch sites whenever any filter or bbox changes ───────────────────────
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();

    setLoading(true);
    setError(null);

    const filters = {
      state: selectedState || undefined,
      district: selectedDistrict || undefined,
      industry_type: selectedIndustryType || undefined,
      status: selectedStatus || undefined,
      bbox: currentBbox ?? undefined,
    };

    const request = activeQuery.trim()
      ? searchIndustries(activeQuery.trim(), filters)
      : fetchIndustries(filters);

    request
      .then(data => {
        setSites(data);
        setLoading(false);
        if (backendStatus !== 'online') setBackendStatus('online');
      })
      .catch(err => {
        const isAbort = err instanceof ApiError
          ? false
          : String(err).toLowerCase().includes('abort');
        if (isAbort) return;
        const msg = err instanceof ApiError ? err.message : String(err);
        setError(msg);
        setLoading(false);
        if (err instanceof ApiError && err.isNetworkError) setBackendStatus('offline');
      });
  }, [activeQuery, selectedState, selectedDistrict, selectedIndustryType, selectedStatus, currentBbox]);

  // ── Site selection ────────────────────────────────────────────────────────
  const handleSelectSite = useCallback(async (siteId: string) => {
    setSelectedSiteId(siteId);
    // First try the already-loaded collection (instant)
    const cached = sites?.features.find(f => f.properties.site_id === siteId) ?? null;
    if (cached) {
      setSelectedSite(cached);
      return;
    }
    // Fallback: fetch from API (handles case where site is outside current bbox)
    try {
      const feature = await fetchIndustrialSite(siteId);
      setSelectedSite(feature);
    } catch {
      setSelectedSite(null);
    }
  }, [sites]);

  // ── Filter handlers ───────────────────────────────────────────────────────
  const handleStateChange = useCallback((state: string) => {
    setSelectedState(state);
    setSelectedDistrict(''); // Always reset district when state changes
  }, []);

  const handleSearch = useCallback(() => {
    setActiveQuery(searchDraft.trim());
  }, [searchDraft]);

  const handleReset = useCallback(() => {
    setSearchDraft('');
    setActiveQuery('');
    setSelectedState('');
    setSelectedDistrict('');
    setSelectedIndustryType('');
    setSelectedStatus('');
    setCurrentBbox(null);
    setSelectedSiteId(null);
    setSelectedSite(null);
  }, []);

  const handleCloseDetails = useCallback(() => {
    setSelectedSiteId(null);
    setSelectedSite(null);
  }, []);

  // ── Derived display values ────────────────────────────────────────────────
  const visibleCount = sites?.features.length ?? 0;
  const isRefreshing = loading && sites !== null; // subsequent fetch while map is visible
  const isInitialLoad = loading && sites === null; // very first load

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        overflow: 'hidden',
        background: 'radial-gradient(ellipse at top left, rgba(14,165,233,0.09) 0%, transparent 50%), linear-gradient(180deg, #f0f7ff 0%, #e8f0fe 100%)',
        fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif",
      }}
    >
      {/* ── Offline banner ───────────────────────────────────────────────── */}
      {backendStatus === 'offline' && (
        <div
          role="alert"
          style={{
            flexShrink: 0,
            background: '#fffbeb',
            borderBottom: '1px solid #fcd34d',
            padding: '6px 18px',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 12,
            color: '#92400e',
            fontWeight: 500,
          }}
        >
          <span>⚠️</span>
          <span>
            Backend unavailable — showing last available data.
            Filters and search will not work until reconnected.
          </span>
          <button
            onClick={() => {
              setBackendStatus('checking');
              checkHealth()
                .then(h => { setBackendStatus('online'); setDataMode(h.data_mode); })
                .catch(() => setBackendStatus('offline'));
            }}
            style={{
              marginLeft: 'auto', padding: '3px 12px', borderRadius: 8,
              border: '1px solid #fcd34d', background: '#fef9c3',
              color: '#92400e', fontSize: 11, fontWeight: 600, cursor: 'pointer',
            }}
          >
            Retry
          </button>
        </div>
      )}
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div style={{ flexShrink: 0, padding: '12px 14px 0' }}>
        <Header
          searchDraft={searchDraft}
          selectedState={selectedState}
          selectedDistrict={selectedDistrict}
          selectedIndustryType={selectedIndustryType}
          selectedStatus={selectedStatus}
          options={options}
          availableDistricts={availableDistricts}
          isLoading={isRefreshing}
          onSearchDraftChange={handleSearchDraftChange}
          onStateChange={handleStateChange}
          onDistrictChange={setSelectedDistrict}
          onIndustryTypeChange={setSelectedIndustryType}
          onStatusChange={setSelectedStatus}
          onSearch={handleSearch}
          onReset={handleReset}
        />
      </div>

      {/* ── Statistics strip ────────────────────────────────────────────── */}
      <div style={{ flexShrink: 0, padding: '10px 14px 0' }}>
        <StatisticsCards
          statistics={statistics}
          onShowAnalytics={() => setShowAnalytics(true)}
        />
      </div>

      {/* ── Status bar ──────────────────────────────────────────────────── */}
      <div style={{
        flexShrink: 0,
        padding: '8px 18px',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        minHeight: 32,
      }}>
        {isRefreshing && (
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            fontSize: 12, color: '#0ea5e9', fontWeight: 500,
          }}>
            <span style={{
              width: 12, height: 12, borderRadius: '50%',
              border: '2px solid #bae6fd', borderTopColor: '#0ea5e9',
              animation: 'spin 0.7s linear infinite', display: 'inline-block',
            }} />
            Refreshing…
          </span>
        )}

        {!isRefreshing && !error && sites && (
          <span style={{ fontSize: 12, color: '#64748b' }}>
            Showing{' '}
            <strong style={{ color: '#0f172a', fontWeight: 700 }}>{visibleCount}</strong>
            {' '}site{visibleCount !== 1 ? 's' : ''}
            {activeQuery && (
              <> matching <em style={{ color: '#0ea5e9' }}>"{activeQuery}"</em></>
            )}
            {selectedState && (
              <> in <strong style={{ color: '#0f172a' }}>{selectedState}</strong></>
            )}
            {selectedDistrict && (
              <> · {selectedDistrict}</>
            )}
          </span>
        )}

        {error && (
          <span style={{ fontSize: 12, color: '#dc2626', fontWeight: 600 }}>
            ⚠ {error}
          </span>
        )}

        {/* Right side: data badge + bbox info */}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
          {currentBbox && (
            <span style={{
              fontSize: 10, color: '#94a3b8',
              padding: '2px 8px', borderRadius: 99,
              background: '#f1f5f9', border: '1px solid #e2e8f0',
            }}>
              bbox active
            </span>
          )}
          {/* Dynamic data-mode badge — value comes from /api/health */}
          <span style={{
            fontSize: 10, fontWeight: 600, textTransform: 'uppercase',
            letterSpacing: '0.07em', padding: '2px 10px', borderRadius: 99,
            background: dataMode === 'postgis'
              ? 'rgba(34,197,94,0.10)'
              : 'rgba(14,165,233,0.08)',
            color: dataMode === 'postgis' ? '#15803d' : '#0369a1',
            border: dataMode === 'postgis'
              ? '1px solid rgba(34,197,94,0.25)'
              : '1px solid rgba(14,165,233,0.2)',
          }}>
            {dataMode === 'postgis' ? '🗄 PostGIS' : '🔬 Synthetic'}
          </span>
        </div>
      </div>

      {/* ── Map area ────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, minHeight: 0, padding: '0 14px 14px' }}>
        <div style={{
          width: '100%', height: '100%',
          borderRadius: 20,
          overflow: 'hidden',
          boxShadow: '0 8px 40px rgba(15,23,42,0.12), 0 2px 8px rgba(15,23,42,0.06)',
          border: '1px solid rgba(255,255,255,0.8)',
          background: '#f0f4f8',
          position: 'relative',
        }}>
          {/* Initial load state — shows before any sites are available */}
          {isInitialLoad && (
            <div style={{
              position: 'absolute', inset: 0, zIndex: 10,
              background: 'rgba(240,244,248,0.92)', backdropFilter: 'blur(4px)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <LoadingState message="Loading industrial sites across India…" />
            </div>
          )}

          {/* Error state — shows when first load fails completely */}
          {error && !sites && (
            <div style={{
              position: 'absolute', inset: 0, zIndex: 10,
              background: 'rgba(240,244,248,0.95)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <ErrorState message={error} onRetry={handleReset} />
            </div>
          )}

          {/* Map — always rendered so Leaflet persists its state */}
          <MapView
            sites={sites}
            selectedSiteId={selectedSiteId}
            onSelectSite={handleSelectSite}
            onBoundsChange={handleBoundsChange}
          />

          {/* Map legend overlay — bottom left */}
          <div style={{
            position: 'absolute', bottom: 16, left: 16, zIndex: 500,
            background: 'rgba(255,255,255,0.93)', backdropFilter: 'blur(10px)',
            borderRadius: 12, padding: '8px 12px',
            boxShadow: '0 2px 12px rgba(15,23,42,0.12)',
            border: '1px solid rgba(255,255,255,0.6)',
            fontSize: 11, fontFamily: "'IBM Plex Sans', sans-serif",
          }}>
            <p style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#94a3b8', margin: '0 0 6px' }}>
              Confidence
            </p>
            {[
              { color: '#22c55e', label: 'High' },
              { color: '#f59e0b', label: 'Medium' },
              { color: '#ef4444', label: 'Low' },
            ].map(item => (
              <div key={item.label} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                <div style={{
                  width: 10, height: 10,
                  borderRadius: '50% 50% 50% 0',
                  transform: 'rotate(-45deg)',
                  background: item.color,
                  flexShrink: 0,
                }} />
                <span style={{ color: '#374151', fontWeight: 500 }}>{item.label}</span>
              </div>
            ))}
          </div>

          {/* Zero results overlay */}
          {!isInitialLoad && sites && sites.features.length === 0 && (
            <div style={{
              position: 'absolute', top: '50%', left: '50%',
              transform: 'translate(-50%, -50%)',
              zIndex: 500, textAlign: 'center',
              background: 'rgba(255,255,255,0.95)', backdropFilter: 'blur(12px)',
              borderRadius: 16, padding: '24px 32px',
              boxShadow: '0 4px 24px rgba(15,23,42,0.12)',
              border: '1px solid #e2e8f0',
            }}>
              <span style={{ fontSize: 36 }}>🔍</span>
              <p style={{ fontSize: 14, fontWeight: 700, color: '#1e293b', margin: '8px 0 4px' }}>
                No sites found
              </p>
              <p style={{ fontSize: 12, color: '#64748b', margin: '0 0 12px', maxWidth: 240 }}>
                Try adjusting your filters or search query, or reset to see all sites.
              </p>
              <button
                onClick={handleReset}
                style={{
                  padding: '7px 18px', borderRadius: 9, border: 'none',
                  background: '#0f172a', color: '#fff', fontSize: 12, fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                Reset Filters
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── Slide-in panels ─────────────────────────────────────────────── */}
      <IndustryDetails site={selectedSite} onClose={handleCloseDetails} />
      <AnalyticsPanel
        statistics={statistics}
        isOpen={showAnalytics}
        onClose={() => setShowAnalytics(false)}
      />

      {/* Backdrop for mobile panel dismissal */}
      {(selectedSite || showAnalytics) && (
        <div
          onClick={() => { handleCloseDetails(); setShowAnalytics(false); }}
          style={{
            position: 'fixed', inset: 0, zIndex: 550,
            background: 'rgba(15,23,42,0.3)',
            backdropFilter: 'blur(2px)',
            display: 'none',
          }}
          className="mobile-backdrop"
        />
      )}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @media (max-width: 640px) {
          .mobile-backdrop { display: block !important; }
        }
      `}</style>
    </div>
  );
}
