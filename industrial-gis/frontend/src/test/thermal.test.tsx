import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ThermalDetails } from '@/components/ThermalDetails';
import { thermalRadius } from '@/components/MapView';
import type { ThermalFeature } from '@/types/industrial';

vi.mock('leaflet', () => ({ default: {} }));
vi.mock('leaflet.markercluster', () => ({}));
vi.mock('leaflet/dist/leaflet.css', () => ({}));
vi.mock('leaflet.markercluster/dist/MarkerCluster.css', () => ({}));
vi.mock('leaflet.markercluster/dist/MarkerCluster.Default.css', () => ({}));

vi.mock('@/services/api', () => ({
  fetchThermalImagery: vi.fn(async () => ({ available: false, reason: 'No Sentinel-2 scene under 30% cloud within 5 days of the last detection (0 candidate(s)).', window_km: 3 })),
}));

const feature = (over: Record<string, unknown> = {}): ThermalFeature => ({
  type: 'Feature',
  geometry: { type: 'Point', coordinates: [85.1, 21.9] },
  properties: {
    source_id: 'ts-2190_8510', thermal_class: 'industrial_persistent',
    thermal_label: 'Persistent industrial thermal source', class_confidence: 'high', class_margin: 0.7,
    class_scores: { industrial_persistent: 0.8, other: 0.1, unmapped_persistent: 0.05 },
    reasons: ['lies on a mapped steel metal (Test Steel)', 'detected on 7 of the last 7 days'],
    alert_level: null, alert_text: null, state: 'Odisha', site_ref: 'way/1', site_name: 'Test Steel',
    site_kind: 'steel_metal', site_distance_m: 240, site_within: true, n_detections: 20, n_days: 7, window_days: 7,
    first_seen: '2026-10-02T06:00:00Z', last_seen: '2026-10-08T21:00:00Z', frp_mean: 9, frp_max: 16, frp_total: 180,
    night_fraction: 0.85, extent_m: 600, lc_dominant: 'built', lc_crop: 0, lc_vegetation: 0.1, lc_built: 0.8,
    lc_bare: 0.1, instruments: 'VIIRS_SNPP_NRT,VIIRS_NOAA20_NRT',
    detections: [
      { detection_id: 'a', time: '2026-10-02T21:00:00Z', instrument: 'VIIRS_SNPP_NRT', frp: 9, confidence: 'nominal', daynight: 'N', lat: 21.9, lon: 85.1 },
      { detection_id: 'b', time: '2026-10-05T08:00:00Z', instrument: 'VIIRS_SNPP_NRT', frp: 16, confidence: 'high', daynight: 'D', lat: 21.9, lon: 85.1 },
    ],
    ...over,
  } as ThermalFeature['properties'],
});

const info = { id: 'industrial_persistent' as const, label: 'x', color: '#dc2626', description: '' };

describe('ThermalDetails', () => {
  it('explains the classification with reasons, scores, facility and land cover', () => {
    render(<ThermalDetails feature={feature()} info={info} gone={false} onClose={() => {}} />);
    const panel = screen.getByLabelText('Thermal source details');
    expect(within(panel).getByRole('heading', { name: 'Persistent industrial thermal source' })).toBeInTheDocument();
    expect(within(panel).getByText('detected on 7 of the last 7 days')).toBeInTheDocument();
    expect(within(panel).getByText(/Test Steel/, { selector: 'div' })).toBeInTheDocument();
    expect(within(panel).getByText(/built dominant/)).toBeInTheDocument();
    expect(within(panel).getByText(/heuristic score, not a verified cause/)).toBeInTheDocument();
    expect(within(panel).getByRole('img', { name: /Detections over time/ })).toBeInTheDocument();
  });

  it('shows an alert banner only when the source has an alert', () => {
    const { rerender } = render(<ThermalDetails feature={feature()} info={info} gone={false} onClose={() => {}} />);
    expect(screen.queryByText(/alert -/i)).not.toBeInTheDocument();
    rerender(<ThermalDetails feature={feature({ alert_level: 'critical', alert_text: 'verify with imagery' })}
                             info={info} gone={false} onClose={() => {}} />);
    expect(screen.getByText(/critical alert/i)).toBeInTheDocument();
    expect(screen.getByText(/verify with imagery/)).toBeInTheDocument();
  });

  it('is honest when the nearest facility is outside its radius or absent', () => {
    const { rerender } = render(<ThermalDetails feature={feature({ site_within: false })} info={info} gone={false} onClose={() => {}} />);
    expect(screen.getByText(/not treated as the source/)).toBeInTheDocument();
    rerender(<ThermalDetails feature={feature({ site_ref: null, site_name: null, site_within: false, site_distance_m: null })}
                             info={info} gone={false} onClose={() => {}} />);
    expect(screen.getByText('None within range')).toBeInTheDocument();
  });

  it('flags a source that aged out of the window', () => {
    render(<ThermalDetails feature={feature()} info={info} gone onClose={() => {}} />);
    expect(screen.getByText(/no detections in the current window any more/)).toBeInTheDocument();
  });
});

describe('thermalRadius', () => {
  it('grows with radiative power and stays within bounds', () => {
    expect(thermalRadius(0)).toBe(4);
    expect(thermalRadius(10)).toBeGreaterThan(thermalRadius(2));
    expect(thermalRadius(100000)).toBe(18);
  });
});
