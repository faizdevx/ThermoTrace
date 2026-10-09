import { describe, expect, it } from 'vitest';
import { geometryBounds, mergeBounds } from '@/utils/geo';
import { formatAge } from '@/components/LiveStatus';
import { buildQuery, exportUrl } from '@/services/api';

describe('geometryBounds', () => {
  it('handles points, polygons and multipolygons', () => {
    expect(geometryBounds({ coordinates: [91.5, 26.2] })).toEqual([91.5, 26.2, 91.5, 26.2]);
    expect(geometryBounds({ coordinates: [[[90, 25], [92, 25], [92, 27], [90, 25]]] })).toEqual([90, 25, 92, 27]);
    expect(geometryBounds({ coordinates: [[[[1, 1], [2, 1], [2, 2], [1, 1]]], [[[5, 5], [6, 5], [6, 7], [5, 5]]]] }))
      .toEqual([1, 1, 6, 7]);
  });
  it('returns null without coordinates', () => {
    expect(geometryBounds(null)).toBeNull();
    expect(geometryBounds({ coordinates: [] })).toBeNull();
  });
  it('merges boxes', () => {
    expect(mergeBounds([[0, 0, 1, 1], null, [2, -1, 3, 0.5]])).toEqual([0, -1, 3, 1]);
    expect(mergeBounds([null])).toBeNull();
  });
});

describe('formatAge', () => {
  it.each([[5, '5 s'], [125, '2 min'], [3 * 3600 + 120, '3 h 2 min'], [90000, '1 d 1 h']])('%s s -> %s', (s, text) => {
    expect(formatAge(s)).toBe(text);
  });
  it('is honest about missing values', () => {
    expect(formatAge(null)).toBe('n/a');
    expect(formatAge(undefined)).toBe('n/a');
  });
});

describe('api query building', () => {
  it('drops empty values and encodes the rest', () => {
    expect(buildQuery({ state: 'Assam', district: '', q: 'tea & co', limit: 5 })).toBe('?state=Assam&q=tea+%26+co&limit=5');
    expect(buildQuery({})).toBe('');
  });
  it('builds an export URL carrying every active filter', () => {
    const url = exportUrl('csv', { state: 'Assam', confidence: 'high' });
    expect(url).toContain('/industries/export?');
    expect(url).toContain('state=Assam');
    expect(url).toContain('confidence=high');
    expect(url).toContain('format=csv');
  });
});
