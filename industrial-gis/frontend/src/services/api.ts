/**
 * Central API client for the India Industrial Intelligence Platform.
 *
 * All API calls go through this module. The base URL is configured via the
 * VITE_API_BASE_URL environment variable (see .env.example).  When the
 * variable is unset the Vite dev proxy routes /api → http://localhost:8000.
 */
import type {
  BoundaryFeature,
  FeatureCollection,
  FilterOptionsResponse,
  IndustrialSiteFeature,
  IndustryQueryFilters,
  StatisticsResponse,
} from '@/types/industrial';

// ── Configuration ─────────────────────────────────────────────────────────────

/** Resolved at build-time from VITE_API_BASE_URL or falls back to '/api'. */
const API_BASE: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? '/api';

const DEFAULT_TIMEOUT_MS = 10_000; // 10 s for regular requests
const HEALTH_TIMEOUT_MS  =  3_000; //  3 s for the health probe

// ── Typed error ───────────────────────────────────────────────────────────────

/**
 * Thrown by every request function on network errors, timeouts, and
 * non-2xx HTTP responses.
 *
 * `status === 0` means a network-level failure (offline, DNS, timeout).
 */
export class ApiError extends Error {
  constructor(
    /** HTTP status code, or 0 for network/timeout errors. */
    public readonly status: number,
    message: string,
    /** The request path that failed. */
    public readonly path: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  /** True if the error is a network/timeout issue (backend may be down). */
  get isNetworkError(): boolean {
    return this.status === 0;
  }
}

// ── Core fetch wrapper ────────────────────────────────────────────────────────

async function requestJson<T>(
  path: string,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const timerId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      signal: controller.signal,
      headers: { Accept: 'application/json' },
    });

    clearTimeout(timerId);

    if (!response.ok) {
      // Try to parse FastAPI's {"detail": "..."} error body
      let message = `HTTP ${response.status}`;
      try {
        const body = (await response.json()) as { detail?: unknown };
        if (typeof body.detail === 'string') {
          message = body.detail;
        } else if (body.detail !== undefined) {
          message = JSON.stringify(body.detail);
        }
      } catch {
        try {
          const text = (await response.text()).trim();
          if (text) message = text;
        } catch { /* keep default */ }
      }
      throw new ApiError(response.status, message, path);
    }

    return (await response.json()) as T;
  } catch (err) {
    clearTimeout(timerId);
    if (err instanceof ApiError) throw err;

    // AbortError = timeout; TypeError = network error
    const message =
      err instanceof DOMException && err.name === 'AbortError'
        ? 'Request timed out'
        : err instanceof Error
          ? err.message
          : 'Unknown network error';

    throw new ApiError(0, message, path);
  }
}

// ── Query builder ─────────────────────────────────────────────────────────────

function buildQuery(
  filters: IndustryQueryFilters & { q?: string; limit?: number },
): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Probe the backend health endpoint.
 * Resolves with `{ status, data_mode }` or rejects with ApiError (status 0)
 * if the backend is unreachable within 3 seconds.
 */
export async function checkHealth(): Promise<{ status: string; data_mode: string }> {
  return requestJson<{ status: string; data_mode: string }>(
    '/health',
    HEALTH_TIMEOUT_MS,
  );
}

export async function fetchIndustries(
  filters: IndustryQueryFilters = {},
): Promise<FeatureCollection<IndustrialSiteFeature>> {
  return requestJson(`/industries${buildQuery(filters)}`);
}

export async function searchIndustries(
  queryText: string,
  filters: IndustryQueryFilters = {},
): Promise<FeatureCollection<IndustrialSiteFeature>> {
  return requestJson(`/industries/search${buildQuery({ ...filters, q: queryText })}`);
}

export async function fetchIndustrialSite(
  siteId: string,
): Promise<IndustrialSiteFeature> {
  return requestJson(`/industries/${encodeURIComponent(siteId)}`);
}

export async function fetchBoundaries(
  level: 'india' | 'states' | 'districts',
): Promise<FeatureCollection<BoundaryFeature>> {
  return requestJson(`/boundaries/${level}`);
}

export async function fetchStatistics(): Promise<StatisticsResponse> {
  return requestJson('/statistics');
}

export async function fetchFilterOptions(): Promise<FilterOptionsResponse> {
  return requestJson('/filters/options');
}
