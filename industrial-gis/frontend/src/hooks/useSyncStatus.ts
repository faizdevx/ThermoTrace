import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchSyncStatus } from '@/services/api';
import type { SyncStatusResponse } from '@/types/industrial';

/** Polls /api/sync/status; call `refresh()` for an immediate update (e.g. on SSE events). */
export function useSyncStatus(intervalMs = 15_000) {
  const [status, setStatus] = useState<SyncStatusResponse | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const seq = useRef(0);

  const refresh = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const next = await fetchSyncStatus();
      if (mine !== seq.current) return; // a newer request already won
      setStatus(next);
      setUnreachable(false);
    } catch {
      if (mine === seq.current) setUnreachable(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => { void refresh(); }, intervalMs);
    return () => clearInterval(id);
  }, [refresh, intervalMs]);

  return { status, unreachable, refresh };
}
