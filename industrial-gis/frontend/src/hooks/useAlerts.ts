import { useCallback, useEffect, useState } from 'react';
import { fetchAlert, fetchAlerts } from '@/services/api';
import type { ThermalAlert } from '@/types/industrial';

const POLL_MS = 60_000;

/** The alert inbox: list (polled, and refetched whenever a new thermal snapshot is announced) plus the open alert. */
export function useAlerts() {
  const [alerts, setAlerts] = useState<ThermalAlert[]>([]);
  const [available, setAvailable] = useState(false);
  const [decisionsEnabled, setDecisionsEnabled] = useState(false);
  const [channels, setChannels] = useState<string[]>([]);
  const [demoReadonly, setDemoReadonly] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ThermalAlert | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick(t => t + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    fetchAlerts(undefined, controller.signal)
      .then(r => { setAlerts(r.alerts); setAvailable(r.available); setDecisionsEnabled(r.decisions_enabled); setChannels(r.channels); setDemoReadonly(!!r.demo_readonly); setError(null); })
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Alerts unavailable'); });
    return () => controller.abort();
  }, [tick]);

  useEffect(() => {
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    if (selectedId == null) { setDetail(null); return; }
    const controller = new AbortController();
    fetchAlert(selectedId, controller.signal)
      .then(setDetail)
      .catch(() => { if (!controller.signal.aborted) setDetail(null); });
    return () => controller.abort();
  }, [selectedId, tick]);

  return { alerts, available, decisionsEnabled, demoReadonly, channels, error, selectedId, select: setSelectedId, detail, refresh };
}

export type AlertsState = ReturnType<typeof useAlerts>;
