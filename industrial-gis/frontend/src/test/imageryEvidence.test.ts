import { describe, expect, it } from 'vitest';
import { imageryEvidence } from '@/components/imageryEvidence';
import type { ThermalImagery } from '@/types/industrial';

const png = 'data:image/png;base64,AAAA';
const full: ThermalImagery = {
  available: true, reason: null, window_km: 3,
  scene: { id: 'S2B_45QVG_20261013_0_L2A', datetime: '2026-10-13T05:02:01+00:00', cloud_cover_percent: 22.4, offset_days: -1.65,
    platform: 'sentinel-2b', chip_cloud_fraction: 0.05 },
  before_scene: { id: 'S2B_45QVG_20261003_0_L2A', datetime: '2026-10-03T05:02:02+00:00', cloud_cover_percent: 21, gap_days: 10 },
  images: { after: { true_colour: png, swir: png }, before: { true_colour: png, swir: png } },
  indices: { nbr_before: 0.62, nbr_after: 0.55, dnbr_mean: 0.07, dnbr_p95: 0.31, burned_fraction: 0.33, valid_fraction: 0.6, severity: 'moderate-low severity' },
  hot_pixels: { count: 14, fraction: 0.0016, rule: 'B12 >= 0.25 and B12 > 1.1 x B11' },
  landsat: { available: true, scene: { id: 'LC09_x', datetime: '2026-10-06T04:43:00+00:00', cloud_cover_percent: 2, platform: 'landsat-9' },
    st_max_k: 323.7, st_excess_k: 14.8 },
};

describe('imageryEvidence', () => {
  it('builds the four chips, dNBR, hot pixels and the Landsat line', () => {
    const e = imageryEvidence(full);
    expect(e.available).toBe(true);
    expect(e.panels.map(p => p.key)).toEqual(['after-tc', 'after-swir', 'before-tc', 'before-swir']);
    expect(e.afterLine).toContain('2026-10-13');
    expect(e.afterLine).toContain('22% scene cloud, 5% over the chip');
    expect(e.afterLine).toContain('S2B_45QVG_20261013_0_L2A');
    expect(e.beforeLine).toContain('before: 2026-10-03 (10 d earlier');
    expect(e.dnbr).toEqual({ value: '+0.31', severity: 'moderate-low severity', burned: '33% of the chip', tone: 'high' });
    expect(e.hotPixels).toBe('14 SWIR hot pixels (0.16% of the chip)');
    expect(e.landsat).toContain('Landsat landsat-9 2026-10-06: surface temperature up to 324 K, 15 K above');
  });

  it('shows only what exists: no earlier scene means no before chips and no dNBR', () => {
    const e = imageryEvidence({ ...full, before_scene: null, images: { after: full.images!.after }, indices: null });
    expect(e.panels.map(p => p.key)).toEqual(['after-tc', 'after-swir']);
    expect(e.dnbr).toBeNull();
    expect(e.beforeLine).toBeNull();
  });

  it('is honest when no scene was found, and still reports a Landsat reading if there is one', () => {
    const e = imageryEvidence({ available: false, reason: 'No Sentinel-2 scene under 30% cloud within 5 days', window_km: 3,
      landsat: full.landsat });
    expect(e.available).toBe(false);
    expect(e.reason).toContain('No Sentinel-2 scene');
    expect(e.panels).toHaveLength(0);
    expect(e.landsat).toContain('Landsat');
  });

  it('still renders the earlier single-scene response shape', () => {
    const e = imageryEvidence({ available: true, reason: null, window_km: 3, true_colour: png, swir: png,
      scene: { id: 'x', datetime: '2026-10-13T05:02:01+00:00', cloud_cover_percent: 10, offset_days: 0.5 } });
    expect(e.panels.map(p => p.key)).toEqual(['after-tc', 'after-swir']);
  });

  it('classifies dNBR tone by the USGS thresholds', () => {
    const tone = (v: number) => imageryEvidence({ ...full, indices: { ...full.indices!, dnbr_p95: v } }).dnbr!.tone;
    expect([0.02, 0.15, 0.5].map(tone)).toEqual(['none', 'low', 'high']);
  });
});
