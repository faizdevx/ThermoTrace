import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ApiError,
  fetchThermalClasses,
  fetchThermalSource,
  fetchThermalSources,
  fetchThermalStats,
  fetchThermalStatus,
} from '@/services/api';
import type {
  FeatureCollection,
  ThermalClassId,
  ThermalClassInfo,
  ThermalFeature,
  ThermalFilters,
  ThermalStats,
  ThermalStatus,
} from '@/types/industrial';

export const WINDOWS: Array<{ label: string; hours: number | undefined }> = [
  { label: 'Last 24 h', hours: 24 },
  { label: 'Last 3 days', hours: 72 },
  { label: 'All (window)', hours: undefined },
];

/** State + data for the thermal overlay. Stale responses are dropped; filters survive refreshes. */
export function useThermal() {
  const [enabled, setEnabled] = useState(true);
  const [classes, setClasses] = useState<ThermalClassInfo[]>([]);
  const [active, setActive] = useState<Set<ThermalClassId>>(new Set());
  const [alertsOnly, setAlertsOnly] = useState(false);
  const [minFrp, setMinFrp] = useState(0);
  const [windowIdx, setWindowIdx] = useState(2);
  const [data, setData] = useState<FeatureCollection<ThermalFeature> | null>(null);
  const [stats, setStats] = useState<ThermalStats | null>(null);
  const [status, setStatus] = useState<ThermalStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<ThermalFeature | null>(null);
  const [selectedGone, setSelectedGone] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const seq = useRef(0);
  const detailSeq = useRef(0);

  // class metadata (legend) once
  useEffect(() => {
    fetchThermalClasses()
      .then(r => {
        setClasses(r.classes);
        setActive(new Set(r.classes.map(c => c.id)));
      })
      .catch(() => { /* legend falls back to empty; data fetch reports the real error */ });
  }, []);

  const filters = useMemo<ThermalFilters>(() => ({
    thermal_class: classes.length && active.size < classes.length ? [...active].join(',') || 'none-selected' : undefined,
    min_frp: minFrp > 0 ? minFrp : undefined,
    alerts_only: alertsOnly || undefined,
    hours: WINDOWS[windowIdx].hours,
  }), [active, classes.length, minFrp, alertsOnly, windowIdx]);

  // sources + stats + status
  useEffect(() => {
    if (!enabled || !classes.length) return;
    const mine = ++seq.current;
    const controller = new AbortController();
    const noneSelected = active.size === 0;
    setLoading(true);
    Promise.all([
      noneSelected ? Promise.resolve(null) : fetchThermalSources(filters, controller.signal),
      fetchThermalStats({ ...filters, thermal_class: undefined, min_frp: undefined, alerts_only: undefined }, controller.signal),
      fetchThermalStatus(controller.signal),
    ])
      .then(([sources, st, status]) => {
        if (mine !== seq.current) return;
        setData(sources ?? { type: 'FeatureCollection', features: [] });
        setStats(st);
        setStatus(status);
        setError(null);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (mine !== seq.current || (err instanceof ApiError && err.aborted)) return;
        setError(err instanceof ApiError ? err.message : String(err));
        setLoading(false);
      });
    return () => controller.abort();
  }, [enabled, classes.length, filters, active.size, refreshToken]);

  // keep an open thermal detail current after each refresh; flag when it aged out
  useEffect(() => {
    if (!selectedId) return;
    const mine = ++detailSeq.current;
    fetchThermalSource(selectedId)
      .then(f => { if (mine === detailSeq.current) { setSelected(f); setSelectedGone(false); } })
      .catch((err: unknown) => {
        if (mine === detailSeq.current && err instanceof ApiError && err.status === 404) setSelectedGone(true);
      });
  }, [selectedId, refreshToken]);

  const refresh = useCallback(() => setRefreshToken(t => t + 1), []);
  const toggleClass = useCallback((id: ThermalClassId) => {
    setActive(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);
  const select = useCallback((id: string | null) => {
    setSelectedId(id);
    setSelectedGone(false);
    if (id === null) setSelected(null);
    else setSelected(prev => (prev?.properties.source_id === id ? prev : data?.features.find(f => f.properties.source_id === id) ?? null));
  }, [data]);

  return {
    enabled, setEnabled, classes, active, toggleClass, alertsOnly, setAlertsOnly, minFrp, setMinFrp,
    windowIdx, setWindowIdx, data, stats, status, error, loading, selectedId, selected, selectedGone,
    select, refresh, filters,
  };
}
