import { describe, expect, it } from 'vitest';
import { cellOpacity, cellSize, dominantClass, DENSITY_ZOOM, splitForZoom, staysPoint } from '@/components/thermalDensity';
import type { ThermalFeature } from '@/types/industrial';

function feat(id: string, cls: string, lat: number, lon: number, frp = 5, alert: string | null = null): ThermalFeature {
  return { type: 'Feature', geometry: { type: 'Point', coordinates: [lon, lat] },
    properties: { source_id: id, thermal_class: cls, frp_max: frp, frp_total: frp, alert_level: alert } } as unknown as ThermalFeature;
}

const fs = [
  feat('c1', 'agricultural_burning', 30.1, 75.1), feat('c2', 'agricultural_burning', 30.4, 75.4), feat('c3', 'agricultural_burning', 30.6, 75.2),
  feat('v1', 'vegetation_fire', 22.2, 80.2), feat('i1', 'industrial_persistent', 23.0, 85.0, 12), feat('a1', 'agricultural_burning', 18.1, 79.9, 40, 'high'),
];

describe('thermal density rendering', () => {
  it('shows every source as a point from the density zoom on', () => {
    const r = splitForZoom(fs, DENSITY_ZOOM);
    expect(r.cells).toHaveLength(0);
    expect(r.points).toHaveLength(fs.length);
  });

  it('at low zoom keeps industrial and alerting sources as points and bins the rest', () => {
    const r = splitForZoom(fs, 5);
    expect(r.points.map(f => f.properties.source_id).sort()).toEqual(['a1', 'i1']);
    expect(r.cells.reduce((n, c) => n + c.count, 0)).toBe(4);
    const punjab = r.cells.find(c => c.count === 3)!;          // 1-degree cell: the three crop fires share it
    expect(punjab.size).toBe(1);
    expect(dominantClass(punjab)).toBe('agricultural_burning');
    expect(punjab.maxFrp).toBe(5);
  });

  it('uses finer cells as the zoom grows and keeps a selected source as a point', () => {
    expect([cellSize(4), cellSize(6), cellSize(7)]).toEqual([1, 0.5, 0.25]);
    const r = splitForZoom(fs, 7, 'c2');
    expect(r.points.map(f => f.properties.source_id)).toContain('c2');
    expect(staysPoint(fs[1], 'c2')).toBe(true);
    expect(staysPoint(fs[1], null)).toBe(false);
  });

  it('scales opacity with the square root of the count within 0.15 - 0.75', () => {
    expect(cellOpacity(1, 100)).toBeCloseTo(0.21, 2);
    expect(cellOpacity(100, 100)).toBeCloseTo(0.75, 2);
    expect(cellOpacity(500, 100)).toBeCloseTo(0.75, 2);
    expect(cellOpacity(0, 0)).toBe(0.15);
  });
});
