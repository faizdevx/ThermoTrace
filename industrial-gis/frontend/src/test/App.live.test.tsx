/**
 * Frontend lifecycle test: the page reflects newly committed data WITHOUT a reload.
 * The API module and the Leaflet map are replaced by controllable fakes; everything else
 * (App state, debouncing, SSE hook, stale-response guards, details panel, status panel) is real.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeEventSource, installFakeEventSource, site } from './helpers';

const api = vi.hoisted(() => ({
  state: { sites: [] as unknown[], revision: 1, siteDetail: new Map<string, unknown>(), delayMs: 0 },
}));

vi.mock('@/components/MapView', () => ({
  EVIDENCE_COLORS: { high: '#0f0', medium: '#ff0', low: '#f00' },
  MapView: ({ sites, onSelectSite, onViewportChange }: any) => {
    // emit a first viewport like Leaflet's whenReady
    (globalThis as any).__emitViewport = () => onViewportChange?.({ bbox: '90,25,93,28', zoom: 8 });
    return (
      <ul data-testid="map-markers">
        {(sites?.features ?? []).map((f: any) => (
          <li key={f.properties.site_id}>
            <button onClick={() => onSelectSite(f.properties.site_id)}>{f.properties.name}</button>
          </li>
        ))}
      </ul>
    );
  },
}));

vi.mock('@/services/api', async () => {
  const actual = await vi.importActual<typeof import('@/services/api')>('@/services/api');
  const wait = () => (api.state.delayMs ? new Promise(r => setTimeout(r, api.state.delayMs)) : Promise.resolve());
  const collection = () => ({
    type: 'FeatureCollection',
    features: [...api.state.sites],
    meta: { total: api.state.sites.length, returned: api.state.sites.length, limit: 2000, offset: 0,
            truncated: false, revision: api.state.revision },
  });
  return {
    ...actual,
    checkHealth: vi.fn(async () => ({ status: 'ok', data_mode: 'postgis', synthetic: false, ready: true,
      database: { status: 'ok' }, dataset: { active_sites: api.state.sites.length } })),
    fetchIndustries: vi.fn(async () => { await wait(); return collection(); }),
    searchIndustries: vi.fn(async () => collection()),
    fetchFilterOptions: vi.fn(async () => ({ states: ['Assam'], districts: ['Kamrup'], industry_types: ['Manufacturing'],
      statuses: ['mapped'], confidence_levels: ['high', 'medium', 'low'] })),
    fetchStatistics: vi.fn(async () => ({
      total_sites: api.state.sites.length, by_state: { Assam: api.state.sites.length }, by_industry_type: {},
      by_status: {}, osm_records: api.state.sites.length, government_records: 0, matched_records: 0,
      unmatched_records: api.state.sites.length, high_confidence_matches: 0,
      confidence_distribution: { medium: api.state.sites.length }, distinct_states: 1, distinct_districts: 1,
      revision: api.state.revision })),
    fetchBoundaries: vi.fn(async () => ({ type: 'FeatureCollection', features: [] })),
    fetchThermalClasses: vi.fn(async () => ({ classes: [{ id: 'industrial_persistent', label: 'Persistent', color: '#dc2626', description: '' }] })),
    fetchThermalSources: vi.fn(async () => ({ type: 'FeatureCollection', features: [] })),
    fetchThermalStats: vi.fn(async () => ({ total_sources: 0, by_class: {}, by_state: {}, alerts: { watch: 0, high: 0, critical: 0 }, total_frp_mw: 0, detections: 0, backend: 'snapshot' })),
    fetchThermalStatus: vi.fn(async () => ({ available: true })),
    fetchThermalSource: vi.fn(async () => { throw new actual.ApiError(404, 'gone', '/thermal/sources/x'); }),
    fetchSyncStatus: vi.fn(async () => ({
      data_mode: 'postgis', api: { alive: true }, database: { status: 'ok' },
      overall: { state: 'live', reason: 'ok' }, revision: api.state.revision, time: new Date().toISOString(),
      sources: [{ source: 'openstreetmap', kind: 'osm_replication', state: 'live', reason: 'ok', enabled: true,
        update_cadence: 'daily', poll_interval_seconds: 300, freshness_target_seconds: 129600,
        last_check_at: new Date().toISOString(), last_check_age_seconds: 5,
        last_source_data_at: new Date(Date.now() - 7200_000).toISOString(), data_age_seconds: 7200,
        source_head_timestamp: null, source_head_published_at: null, lag_behind_source_seconds: 0,
        replication_steps_behind: 0, last_commit_at: null, last_success_at: null, consecutive_failures: 0,
        next_retry_at: null, last_error: null, totals: {} }] })),
    fetchIndustrialSite: vi.fn(async (id: string) => {
      const found = api.state.siteDetail.get(id) ?? api.state.sites.find((s: any) => s.properties.site_id === id);
      if (!found) throw new actual.ApiError(404, 'Industrial site not found', `/industries/${id}`);
      return found;
    }),
  };
});

import App from '@/App';

async function settle(ms = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
  await act(async () => { await vi.advanceTimersByTimeAsync(20); });   // let state updates triggered by timers commit
}

describe('App live updates (no page reload)', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    installFakeEventSource();
    api.state.sites = [site('1', { name: 'Alpha Works' }), site('2', { name: 'Beta Mills' })];
    api.state.revision = 1;
    api.state.siteDetail = new Map();
    api.state.delayMs = 0;
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function boot() {
    render(<App />);
    await settle(10);
    act(() => { (globalThis as any).__emitViewport(); });
    await settle(600);                                   // viewport debounce + fetch
    const es = FakeEventSource.last;
    act(() => { es.open(); es.emit('hello', { revision: 1 }); });
    await settle(10);
  }

  it('shows the initial dataset and the live status', async () => {
    await boot();
    expect(screen.getByText('Alpha Works')).toBeInTheDocument();
    expect(screen.getByText('Beta Mills')).toBeInTheDocument();
    expect(screen.getByLabelText(/Synchronization status: Live/)).toBeInTheDocument();
  });

  it('adds a newly committed site to the map, counters and statistics without reloading', async () => {
    await boot();
    api.state.sites = [...api.state.sites, site('3', { name: 'Gamma Foundry' })];
    api.state.revision = 2;
    act(() => FakeEventSource.last.emit('revision', {
      revision: 2, created: 1, committed_at: new Date().toISOString() }));
    expect(screen.queryByText('Gamma Foundry')).not.toBeInTheDocument();      // not before the debounce batches it
    await settle(1600);
    expect(screen.getByText('Gamma Foundry')).toBeInTheDocument();
    expect(screen.getByText('Data updated (revision 2)')).toBeInTheDocument();
    expect(document.getElementById('visible-count')).toHaveTextContent('3');
  });

  it('batches a burst of notifications into one refresh', async () => {
    await boot();
    const { fetchIndustries } = await import('@/services/api');
    const before = vi.mocked(fetchIndustries).mock.calls.length;
    api.state.sites = [...api.state.sites, site('9', { name: 'Burst Plant' })];
    for (const rev of [2, 3, 4]) {
      act(() => FakeEventSource.last.emit('revision', { revision: rev, committed_at: new Date().toISOString() }));
    }
    await settle(2000);
    expect(vi.mocked(fetchIndustries).mock.calls.length - before).toBe(1);
    expect(screen.getByText('Burst Plant')).toBeInTheDocument();
  });

  it('updates an open details panel when the selected site changes, and flags it when it is removed', async () => {
    await boot();
    fireEvent.click(screen.getByText('Alpha Works'));
    await settle(10);
    const panel = screen.getByLabelText('Industrial site details');
    expect(within(panel).getByRole('heading', { name: 'Alpha Works' })).toBeInTheDocument();

    api.state.sites = [site('1', { name: 'Alpha Works (renamed upstream)' }), api.state.sites[1]];
    api.state.revision = 2;
    act(() => FakeEventSource.last.emit('revision', { revision: 2, committed_at: new Date().toISOString() }));
    await settle(1700);
    expect(within(panel).getByRole('heading', { name: 'Alpha Works (renamed upstream)' })).toBeInTheDocument();

    api.state.sites = [api.state.sites[1]];                        // deleted in the source
    api.state.revision = 3;
    act(() => FakeEventSource.last.emit('revision', { revision: 3, committed_at: new Date().toISOString() }));
    await settle(1700);
    expect(within(panel).getByRole('alert')).toHaveTextContent(/no longer in the dataset/i);
    expect(screen.queryByRole('button', { name: 'Alpha Works (renamed upstream)' })).not.toBeInTheDocument();
  });

  it('keeps the user filters and never lets a slow older response overwrite a newer one', async () => {
    await boot();
    const { fetchIndustries } = await import('@/services/api');
    const calls: Array<(v: unknown) => void> = [];
    const original = vi.mocked(fetchIndustries).getMockImplementation()!;
    vi.mocked(fetchIndustries).mockImplementation(() => new Promise(resolve => {
      calls.push(resolve as (v: unknown) => void);
    }));
    const collection = (names: string[]) => ({
      type: 'FeatureCollection', features: names.map((n, i) => site(`x${i}`, { name: n })),
      meta: { total: names.length, returned: names.length, limit: 2000, offset: 0, truncated: false, revision: 1 } });

    act(() => { (globalThis as any).__emitViewport(); });
    await settle(600);                                             // request #1 (slow)
    const stateSelect = document.getElementById('filter-state')!;
    await act(async () => {
      const select = document.getElementById('filter-state') as HTMLSelectElement;
      select.value = 'Assam';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await settle(10);                                              // request #2 (newer)
    expect(stateSelect).toBeTruthy();
    expect(calls.length).toBeGreaterThanOrEqual(2);
    await act(async () => { calls[calls.length - 1](collection(['NEWER RESULT'])); });
    await settle(0);
    await act(async () => { calls[0](collection(['STALE RESULT'])); });
    await settle(0);
    expect(screen.queryByText('STALE RESULT')).not.toBeInTheDocument();
    expect(screen.getByText('NEWER RESULT')).toBeInTheDocument();
    expect((document.getElementById('filter-state') as HTMLSelectElement).value).toBe('Assam');   // filter preserved
    vi.mocked(fetchIndustries).mockImplementation(original);
  });

  it('falls back to polling and still refreshes when the event stream is unavailable', async () => {
    await boot();
    const { fetchSyncStatus } = await import('@/services/api');
    act(() => { FakeEventSource.last.fail(); FakeEventSource.last.fail(); });
    await settle(0);
    api.state.sites = [...api.state.sites, site('5', { name: 'Polled Plant' })];
    api.state.revision = 2;
    await settle(10_500);
    expect(vi.mocked(fetchSyncStatus)).toHaveBeenCalled();
    await settle(1700);

    expect(screen.getByText('Polled Plant')).toBeInTheDocument();
  });

  it('reports the empty-database state instead of pretending to have data', async () => {
    api.state.sites = [];
    const api2 = await import('@/services/api');
    vi.mocked(api2.checkHealth).mockResolvedValueOnce({ status: 'ok', data_mode: 'postgis', synthetic: false, ready: false,
      database: { status: 'ok' }, dataset: { active_sites: 0 } } as never);
    await boot();
    expect(screen.getByText('No data imported yet')).toBeInTheDocument();
  });

  it('shows a clear banner when the database is down', async () => {
    const api2 = await import('@/services/api');
    vi.mocked(api2.checkHealth).mockResolvedValueOnce({ status: 'ok', data_mode: 'postgis', synthetic: false, ready: false,
      database: { status: 'unavailable' } } as never);
    await boot();
    expect(screen.getByText(/database is unavailable/i)).toBeInTheDocument();
  });
});
