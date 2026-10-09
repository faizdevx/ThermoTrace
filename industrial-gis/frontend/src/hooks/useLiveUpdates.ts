import { useEffect, useRef, useState } from 'react';
import { API_BASE, fetchSyncStatus } from '@/services/api';
import type { RevisionEvent } from '@/types/industrial';

export type LiveConnection = 'connecting' | 'sse' | 'polling' | 'offline';

interface Options {
  /** Called once per newly committed data revision (never for the first baseline). */
  onRevision: (event: RevisionEvent, receivedAtMs: number) => void;
  /** Called when a sync job changes status (cheap signal to refresh the status panel). */
  onSyncEvent?: () => void;
  /** Called when a new thermal (FIRMS) snapshot was published. */
  onThermal?: () => void;
}

const POLL_INTERVAL_MS = 10_000;
const SSE_RETRY_MS = 30_000;

/**
 * Subscribes to GET /api/events (Server-Sent Events). If the stream cannot be held open,
 * falls back to polling /api/sync/status for revision changes and periodically retries SSE.
 * Notifications carry only a revision number - the caller refetches the data it shows.
 */
export function useLiveUpdates({ onRevision, onSyncEvent, onThermal }: Options) {
  const [connection, setConnection] = useState<LiveConnection>('connecting');
  const [revision, setRevision] = useState<number | null>(null);
  const revisionRef = useRef<number | null>(null);
  const onRevisionRef = useRef(onRevision);
  const onSyncRef = useRef(onSyncEvent);
  const onThermalRef = useRef(onThermal);
  useEffect(() => { onThermalRef.current = onThermal; }, [onThermal]);
  useEffect(() => { onRevisionRef.current = onRevision; }, [onRevision]);
  useEffect(() => { onSyncRef.current = onSyncEvent; }, [onSyncEvent]);

  useEffect(() => {
    let cancelled = false;
    let source: EventSource | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let errorBurst = 0;

    const accept = (next: number | null | undefined, event: RevisionEvent | null) => {
      if (next === null || next === undefined) return;
      const previous = revisionRef.current;
      revisionRef.current = next;
      setRevision(next);
      // First value is a baseline; later increases (or a hello after reconnect that moved) are changes.
      if (previous !== null && next !== previous) {
        onRevisionRef.current(event ?? { revision: next }, Date.now());
      }
    };

    const stopPolling = () => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } };

    const startPolling = () => {
      if (pollTimer || cancelled) return;
      setConnection('polling');
      const tick = async () => {
        try {
          const status = await fetchSyncStatus();
          if (!cancelled) accept(status.revision, null);
          if (!cancelled) setConnection(c => (c === 'offline' ? 'polling' : c));
        } catch {
          if (!cancelled) setConnection('offline');
        }
      };
      void tick();
      pollTimer = setInterval(tick, POLL_INTERVAL_MS);
      retryTimer = setTimeout(openStream, SSE_RETRY_MS);
    };

    function openStream() {
      if (cancelled || typeof EventSource === 'undefined') { startPolling(); return; }
      source?.close();
      const es = new EventSource(`${API_BASE}/events`);
      source = es;
      es.onopen = () => {
        errorBurst = 0;
        stopPolling();
        if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
        setConnection('sse');
      };
      es.addEventListener('hello', ev => {
        try { accept((JSON.parse((ev as MessageEvent).data) as { revision: number | null }).revision, null); }
        catch { /* ignore malformed */ }
      });
      es.addEventListener('revision', ev => {
        try {
          const data = JSON.parse((ev as MessageEvent).data) as RevisionEvent;
          accept(data.revision, data);
        } catch { /* ignore malformed */ }
      });
      es.addEventListener('sync', () => onSyncRef.current?.());
      es.addEventListener('thermal', () => onThermalRef.current?.());
      es.onerror = () => {
        errorBurst += 1;
        // Browser auto-reconnects; after repeated failures degrade to polling and keep retrying SSE.
        if (errorBurst >= 2 || es.readyState === EventSource.CLOSED) {
          es.close();
          source = null;
          startPolling();
        } else {
          setConnection('connecting');
        }
      };
    }

    openStream();
    return () => {
      cancelled = true;
      source?.close();
      stopPolling();
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, []);

  return { connection, revision };
}
