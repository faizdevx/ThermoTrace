/**
 * Central API client for ThermoTrace.
 *
 * The base URL comes from VITE_API_BASE_URL; when unset, '/api' is used (Vite dev proxy or
 * the nginx container proxy forward it to the FastAPI backend).
 */
import type {
  AuditEntry,
  AuthMe,
  AuthUser,
  DashboardData,
  AlertDecision,
  AlertList,
  ThermalAlert,
  BoundaryFeature,
  FeatureCollection,
  FilterOptionsResponse,
  HealthResponse,
  IndustrialSiteFeature,
  IndustryQueryFilters,
  StatisticsResponse,
  SyncStatusResponse,
  ThermalClassInfo,
  ThermalFeature,
  ThermalFilters,
  ThermalImagery,
  ThermalStats,
  ThermalStatus,
} from '@/types/industrial';

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? '/api';

const DEFAULT_TIMEOUT_MS = 15_000;
const HEALTH_TIMEOUT_MS = 4_000;

export class ApiError extends Error {
  constructor(
    /** HTTP status code, or 0 for network/timeout errors. */
    public readonly status: number,
    message: string,
    public readonly path: string,
    /** True when the caller cancelled the request (not an error to display). */
    public readonly aborted = false,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  get isNetworkError(): boolean {
    return this.status === 0 && !this.aborted;
  }

  /** 503 from the API = database/infrastructure problem rather than a bad request. */
  get isServerUnavailable(): boolean {
    return this.status === 503;
  }
}

async function requestJson<T>(
  path: string,
  options: { timeoutMs?: number; signal?: AbortSignal; method?: 'GET' | 'POST' | 'PATCH'; body?: unknown; headers?: Record<string, string> } = {},
): Promise<T> {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  let timedOut = false;
  const timerId = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  const onCallerAbort = () => controller.abort();
  options.signal?.addEventListener('abort', onCallerAbort);

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      signal: controller.signal,
      method: options.method ?? 'GET',
      credentials: 'same-origin',
      headers: { Accept: 'application/json', 'X-Requested-With': 'ThermoTrace', ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const body = (await response.json()) as { detail?: unknown };
        if (typeof body.detail === 'string') message = body.detail;
        else if (body.detail !== undefined) message = JSON.stringify(body.detail);
      } catch { /* keep default */ }
      if (response.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('tt-auth-required'));
      throw new ApiError(response.status, message, path);
    }
    return (await response.json()) as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (options.signal?.aborted) throw new ApiError(0, 'Request cancelled', path, true);
    const message = timedOut
      ? 'Request timed out'
      : err instanceof Error ? err.message : 'Unknown network error';
    throw new ApiError(0, message, path);
  } finally {
    clearTimeout(timerId);
    options.signal?.removeEventListener('abort', onCallerAbort);
  }
}

type QueryFilters = IndustryQueryFilters & { q?: string; limit?: number; offset?: number; geometry?: string };

export function buildQuery(filters: QueryFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

export function checkHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return requestJson('/health', { timeoutMs: HEALTH_TIMEOUT_MS, signal });
}

export function fetchIndustries(
  filters: QueryFilters = {},
  signal?: AbortSignal,
): Promise<FeatureCollection<IndustrialSiteFeature>> {
  return requestJson(`/industries${buildQuery(filters)}`, { signal });
}

export function searchIndustries(
  queryText: string,
  filters: QueryFilters = {},
  signal?: AbortSignal,
): Promise<FeatureCollection<IndustrialSiteFeature>> {
  return requestJson(`/industries/search${buildQuery({ ...filters, q: queryText })}`, { signal });
}

export function fetchIndustrialSite(siteId: string, signal?: AbortSignal): Promise<IndustrialSiteFeature> {
  return requestJson(`/industries/${encodeURIComponent(siteId)}`, { signal });
}

export function fetchBoundaries(
  level: 'india' | 'states' | 'districts',
  params: { state?: string; bbox?: string; simplify?: number } = {},
  signal?: AbortSignal,
): Promise<FeatureCollection<BoundaryFeature>> {
  return requestJson(`/boundaries/${level}${buildQuery(params as QueryFilters)}`, { signal });
}

export function fetchStatistics(
  filters: IndustryQueryFilters & { q?: string } = {},
  signal?: AbortSignal,
): Promise<StatisticsResponse> {
  return requestJson(`/statistics${buildQuery(filters)}`, { signal });
}

export function fetchFilterOptions(state?: string, signal?: AbortSignal): Promise<FilterOptionsResponse> {
  return requestJson(`/filters/options${buildQuery({ state } as QueryFilters)}`, { signal });
}

export function fetchSyncStatus(signal?: AbortSignal): Promise<SyncStatusResponse> {
  return requestJson('/sync/status', { timeoutMs: HEALTH_TIMEOUT_MS * 2, signal });
}

/** Absolute-or-relative URL for a full (unpaginated) export of everything matching the filters. */
export function exportUrl(format: 'csv' | 'geojson', filters: IndustryQueryFilters & { q?: string }): string {
  return `${API_BASE}/industries/export${buildQuery({ ...filters, format } as QueryFilters)}`;
}

// ── Thermal sources ───────────────────────────────────────────────────────────

export function fetchThermalClasses(signal?: AbortSignal): Promise<{ classes: ThermalClassInfo[] }> {
  return requestJson('/thermal/classes', { signal });
}

export function fetchThermalStatus(signal?: AbortSignal): Promise<ThermalStatus> {
  return requestJson('/thermal/status', { timeoutMs: HEALTH_TIMEOUT_MS * 2, signal });
}

export function fetchFacilities(signal?: AbortSignal): Promise<import('@/types/industrial').FacilityCollection> {
  return requestJson('/facilities?limit=20000', { signal });
}

export function fetchFacility(id: string, signal?: AbortSignal): Promise<import('@/types/industrial').FacilityDetail> {
  return requestJson(`/facilities/${encodeURIComponent(id)}`, { signal });
}

export function fetchThermalSources(
  filters: ThermalFilters & { limit?: number } = {},
  signal?: AbortSignal,
): Promise<FeatureCollection<ThermalFeature>> {
  return requestJson(`/thermal/sources${buildQuery({ limit: 5000, ...filters } as QueryFilters)}`, { signal });
}

export function fetchThermalSource(id: string, signal?: AbortSignal): Promise<ThermalFeature> {
  return requestJson(`/thermal/sources/${encodeURIComponent(id)}`, { signal });
}

export function fetchThermalStats(filters: ThermalFilters = {}, signal?: AbortSignal): Promise<ThermalStats> {
  return requestJson(`/thermal/stats${buildQuery(filters as QueryFilters)}`, { signal });
}

export function thermalExportUrl(format: 'csv' | 'geojson', filters: ThermalFilters): string {
  return `${API_BASE}/thermal/export${buildQuery({ ...filters, format } as QueryFilters)}`;
}

export function fetchThermalImagery(id: string, signal?: AbortSignal): Promise<ThermalImagery> {
  return requestJson(`/thermal/sources/${encodeURIComponent(id)}/imagery`, { timeoutMs: 90_000, signal });
}

export function fetchAlerts(status?: string, signal?: AbortSignal): Promise<AlertList> {
  return requestJson(`/alerts${buildQuery({ status } as QueryFilters)}`, { signal });
}

export function fetchAlert(id: number, signal?: AbortSignal): Promise<ThermalAlert> {
  return requestJson(`/alerts/${id}`, { signal });
}

export function postAlertDecision(
  id: number, decision: AlertDecision, note: string, analyst: string, token: string,
): Promise<{ alert_id: number; decision: AlertDecision; label_id: string; label: string }> {
  return requestJson(`/alerts/${id}/decision`, {
    method: 'POST', body: { decision, note, analyst }, headers: token ? { 'X-Analyst-Token': token } : {},
  });
}

export function fetchDashboard(signal?: AbortSignal): Promise<DashboardData> {
  return requestJson('/dashboard', { signal });
}

// ---- Phase 9: accounts, roles, audit ---------------------------------------------------------------------------------------
export function fetchMe(signal?: AbortSignal): Promise<AuthMe> {
  return requestJson('/auth/me', { signal });
}

export function login(username: string, password: string): Promise<{ user: AuthUser }> {
  return requestJson('/auth/login', { method: 'POST', body: { username, password } });
}

export function logout(): Promise<{ ok: boolean }> {
  return requestJson('/auth/logout', { method: 'POST', body: {} });
}

export function changePassword(current: string, next: string): Promise<{ ok: boolean }> {
  return requestJson('/auth/password', { method: 'POST', body: { current, new: next } });
}

export function fetchUsers(signal?: AbortSignal): Promise<{ users: AuthUser[] }> {
  return requestJson('/admin/users', { signal });
}

export function createUser(body: { username: string; password: string; role: string; display_name?: string }): Promise<{ user: AuthUser }> {
  return requestJson('/admin/users', { method: 'POST', body });
}

export function updateUser(id: number, body: Partial<{ role: string; active: boolean; password: string; display_name: string }>): Promise<{ user: AuthUser }> {
  return requestJson(`/admin/users/${id}`, { method: 'PATCH', body });
}

export function fetchAudit(params: { limit?: number; before?: number; action?: string; actor?: string } = {}, signal?: AbortSignal): Promise<{ entries: AuditEntry[] }> {
  return requestJson(`/admin/audit${buildQuery(params as QueryFilters)}`, { signal });
}

export function verifyAudit(): Promise<{ ok: boolean; rows: number; first_bad_id: number | null; head: string | null }> {
  return requestJson('/admin/audit/verify');
}
