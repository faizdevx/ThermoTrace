import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useLiveUpdates } from '@/hooks/useLiveUpdates';
import { FakeEventSource, installFakeEventSource } from './helpers';

vi.mock('@/services/api', () => ({
  API_BASE: '/api',
  fetchSyncStatus: vi.fn(),
}));
import { fetchSyncStatus } from '@/services/api';

describe('useLiveUpdates', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    installFakeEventSource();
    vi.mocked(fetchSyncStatus).mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('treats the first revision as a baseline and reports later revisions once each', () => {
    const onRevision = vi.fn();
    const { result } = renderHook(() => useLiveUpdates({ onRevision }));
    const es = FakeEventSource.last;
    act(() => { es.open(); es.emit('hello', { revision: 5 }); });
    expect(result.current.connection).toBe('sse');
    expect(result.current.revision).toBe(5);
    expect(onRevision).not.toHaveBeenCalled();

    act(() => es.emit('revision', { revision: 6, created: 2, committed_at: '2026-10-09T10:00:00Z' }));
    expect(onRevision).toHaveBeenCalledTimes(1);
    expect(onRevision.mock.calls[0][0]).toMatchObject({ revision: 6, created: 2 });

    act(() => es.emit('revision', { revision: 6 }));          // duplicate delivery -> ignored
    expect(onRevision).toHaveBeenCalledTimes(1);
  });

  it('refreshes after a reconnect when the hello revision moved while disconnected', () => {
    const onRevision = vi.fn();
    renderHook(() => useLiveUpdates({ onRevision }));
    const first = FakeEventSource.last;
    act(() => { first.open(); first.emit('hello', { revision: 1 }); });
    act(() => first.emit('hello', { revision: 4 }));          // browser auto-reconnected; server is ahead
    expect(onRevision).toHaveBeenCalledWith({ revision: 4 }, expect.any(Number));
  });

  it('falls back to polling when the stream keeps failing, and still detects changes', async () => {
    vi.mocked(fetchSyncStatus)
      .mockResolvedValueOnce({ revision: 10 } as never)
      .mockResolvedValue({ revision: 11 } as never);
    const onRevision = vi.fn();
    const { result } = renderHook(() => useLiveUpdates({ onRevision }));
    const es = FakeEventSource.last;
    act(() => { es.fail(); es.fail(); });
    expect(result.current.connection).toBe('polling');
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.revision).toBe(10);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(onRevision).toHaveBeenCalledTimes(1);
    expect(onRevision.mock.calls[0][0].revision).toBe(11);
  });

  it('reports offline when neither stream nor polling can reach the API, then retries SSE', async () => {
    vi.mocked(fetchSyncStatus).mockRejectedValue(new Error('down'));
    const { result } = renderHook(() => useLiveUpdates({ onRevision: vi.fn() }));
    act(() => { FakeEventSource.last.fail(); FakeEventSource.last.fail(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.connection).toBe('offline');
    const before = FakeEventSource.instances.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(31_000); });
    expect(FakeEventSource.instances.length).toBeGreaterThan(before);   // SSE reconnection attempt
  });
});
