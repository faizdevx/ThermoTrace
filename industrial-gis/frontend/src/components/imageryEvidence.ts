import type { ThermalImagery } from '@/types/industrial';

export interface ImageryEvidence {
  available: boolean;
  reason: string | null;
  afterLine: string | null;
  beforeLine: string | null;
  dnbr: { value: string; severity: string; burned: string; tone: 'none' | 'low' | 'high' } | null;
  hotPixels: string | null;
  landsat: string | null;
  panels: { key: string; title: string; src: string }[];
}

const day = (iso: string) => iso.slice(0, 10);

/** What the imagery response says, in the words the panel shows. Missing pieces stay null: nothing is invented. */
export function imageryEvidence(d: ThermalImagery): ImageryEvidence {
  if (!d.available || !d.scene) {
    return { available: false, reason: d.reason ?? 'No Sentinel-2 scene available', afterLine: null, beforeLine: null, dnbr: null,
      hotPixels: null, landsat: d.landsat?.available ? landsatLine(d) : null, panels: [] };
  }
  const s = d.scene;
  const chipCloud = s.chip_cloud_fraction != null ? `, ${Math.round(s.chip_cloud_fraction * 100)}% over the chip` : '';
  const afterLine = `${s.platform ?? 'Sentinel-2'} ${day(s.datetime)} (${(s.offset_days ?? 0) > 0 ? '+' : ''}${(s.offset_days ?? 0).toFixed(1)} d from `
    + `the last detection) - ${s.cloud_cover_percent.toFixed(0)}% scene cloud${chipCloud} - ${s.id}`;
  const b = d.before_scene;
  const beforeLine = b ? `before: ${day(b.datetime)} (${b.gap_days?.toFixed(0) ?? '?'} d earlier, ${b.cloud_cover_percent.toFixed(0)}% cloud) - ${b.id}` : null;
  const idx = d.indices;
  const dnbr = idx ? {
    value: `${idx.dnbr_p95 >= 0 ? '+' : ''}${idx.dnbr_p95.toFixed(2)}`, severity: idx.severity,
    burned: `${Math.round(idx.burned_fraction * 100)}% of the chip`,
    tone: (idx.dnbr_p95 >= 0.27 ? 'high' : idx.dnbr_p95 >= 0.1 ? 'low' : 'none') as 'none' | 'low' | 'high',
  } : null;
  const hp = d.hot_pixels;
  const hotPixels = hp ? (hp.count > 0 ? `${hp.count} SWIR hot pixel${hp.count === 1 ? '' : 's'} (${(hp.fraction * 100).toFixed(2)}% of the chip)`
    : 'no SWIR hot pixels') : null;
  const panels: ImageryEvidence['panels'] = [];
  const after = d.images?.after, before = d.images?.before;
  if (after) {
    panels.push({ key: 'after-tc', title: `After ${day(s.datetime)} - true colour`, src: after.true_colour });
    panels.push({ key: 'after-swir', title: `After ${day(s.datetime)} - SWIR (flames glow orange)`, src: after.swir });
  } else if (d.true_colour && d.swir) {                      // earlier API shape
    panels.push({ key: 'after-tc', title: 'True colour', src: d.true_colour });
    panels.push({ key: 'after-swir', title: 'SWIR (flames glow orange)', src: d.swir });
  }
  if (before && b) {
    panels.push({ key: 'before-tc', title: `Before ${day(b.datetime)} - true colour`, src: before.true_colour });
    panels.push({ key: 'before-swir', title: `Before ${day(b.datetime)} - SWIR`, src: before.swir });
  }
  return { available: true, reason: null, afterLine, beforeLine, dnbr, hotPixels, landsat: landsatLine(d), panels };
}

function landsatLine(d: ThermalImagery): string | null {
  const l = d.landsat;
  if (!l) return null;
  if (!l.available || !l.scene) return l.reason ?? null;
  return `Landsat ${l.scene.platform ?? '8/9'} ${day(l.scene.datetime)}: surface temperature up to ${l.st_max_k?.toFixed(0)} K, `
    + `${l.st_excess_k?.toFixed(0)} K above the chip median (${l.scene.cloud_cover_percent.toFixed(0)}% cloud)`;
}
