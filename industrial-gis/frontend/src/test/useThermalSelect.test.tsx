import { describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useThermal } from '@/hooks/useThermal';

// the static site's list carries only map fields; the detail endpoint returns the full record
const slim = { type: 'Feature', geometry: { type: 'Point', coordinates: [80, 20] },
  properties: { source_id: 's1', thermal_class: 'industrial_persistent', frp_max: 9, last_seen: '2026-11-17T09:00:00Z' } };
const full = { ...slim, properties: { ...slim.properties, reasons: ['lies on a mapped refinery'], class_scores: {} } };
let releaseDetail: (v: unknown) => void = () => {};

vi.mock('@/services/api', () => ({
  ApiError: class extends Error {},
  fetchThermalClasses: vi.fn(async () => ({ classes: [{ id: 'industrial_persistent', label: 'x', color: '#f00', description: '' }] })),
  fetchThermalSources: vi.fn(async () => ({ type: 'FeatureCollection', features: [slim] })),
  fetchThermalStats: vi.fn(async () => ({ total_sources: 1, detections: 1, by_class: {}, alerts: { watch: 0, high: 0, critical: 0 } })),
  fetchThermalStatus: vi.fn(async () => ({})),
  fetchThermalSource: vi.fn(() => new Promise(r => { releaseDetail = r; })),
}));

describe('useThermal.select', () => {
  it('never hands the detail panel an incomplete list record (crashed the public site)', async () => {
    const { result } = renderHook(() => useThermal());
    await waitFor(() => expect(result.current.data?.features).toHaveLength(1));
    act(() => result.current.select('s1'));
    expect(result.current.selected).toBeNull();               // slim record withheld
    await act(async () => { releaseDetail(full); });
    await waitFor(() => expect(result.current.selected?.properties.reasons).toEqual(['lies on a mapped refinery']));
  });
});
