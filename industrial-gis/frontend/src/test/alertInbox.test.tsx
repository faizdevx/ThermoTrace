import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AlertInbox } from '@/components/AlertInbox';
import type { AlertsState } from '@/hooks/useAlerts';
import type { ThermalAlert } from '@/types/industrial';

const post = vi.fn();
vi.mock('@/services/api', () => ({ API_BASE: '/api', postAlertDecision: (...a: unknown[]) => post(...a) }));

const alert: ThermalAlert = {
  alert_id: 5, facility_key: 'osm:1', source_id: 's1', site_ref: 'osm:1', site_name: 'Plant A', site_kind: 'steel_plant', state: 'Odisha',
  trigger: 'p_fire', severity: 'high', p_fire: 0.7, frp_max: 60, frp_z: null, first_seen: '2026-10-16T01:00:00Z', last_seen: '2026-10-16T03:00:00Z',
  peak_frp: 60, peak_at: '2026-10-16T02:00:00Z', summary: 'HIGH: possible industrial fire at Plant A', reasons: null, imagery: null, status: 'open',
  delivery: { telegram: { ok: true, at: 'x', error: null } }, is_test: false, created_at: '2026-10-16T03:05:00Z', updated_at: '2026-10-16T03:05:00Z', lat: 20, lon: 85,
  timeline: [
    { at: '2026-10-16T01:00:00Z', kind: 'first_detection', text: 'First detection of this source' },
    { at: '2026-10-16T02:00:00Z', kind: 'peak', text: 'Peak power 60.0 MW' },
    { at: '2026-10-16T03:05:00Z', kind: 'alert', text: 'Alert raised (high, trigger p_fire)' },
  ],
};

function state(over: Partial<AlertsState> = {}): AlertsState {
  return { alerts: [alert], available: true, decisionsEnabled: true, channels: ['telegram'], error: null, selectedId: 5, select: vi.fn(),
    detail: alert, refresh: vi.fn(), ...over } as AlertsState;
}

afterEach(() => { cleanup(); post.mockReset(); sessionStorage.clear(); });

describe('AlertInbox', () => {
  it('lists alerts and shows the timeline with first detection, peak and alert of the open one', () => {
    render(<AlertInbox alerts={state()} open onClose={() => {}} onLocate={() => {}} />);
    expect(screen.getByText('Plant A')).toBeTruthy();
    const tl = screen.getByTestId('alert-timeline').textContent ?? '';
    expect(tl).toMatch(/First detection/);
    expect(tl).toMatch(/Peak power 60.0 MW/);
    expect(screen.getByText(/Delivery: telegram sent/)).toBeTruthy();
  });

  it('keeps the decision buttons disabled until a token is entered, then posts and reports the label', async () => {
    post.mockResolvedValue({ alert_id: 5, decision: 'confirmed', label_id: 'analyst-alert-5', label: 'industrial_fire' });
    const st = state();
    render(<AlertInbox alerts={st} open onClose={() => {}} onLocate={() => {}} />);
    const confirm = screen.getByText('Confirm fire') as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(screen.getByTestId('decide-hint').textContent).toMatch(/token/);
    fireEvent.change(screen.getByLabelText('Analyst token'), { target: { value: 'tok' } });
    fireEvent.change(screen.getByLabelText('Analyst name'), { target: { value: 'asha' } });
    fireEvent.change(screen.getByLabelText('Note'), { target: { value: 'smoke plume visible' } });
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    await waitFor(() => expect(post).toHaveBeenCalledWith(5, 'confirmed', 'smoke plume visible', 'asha', 'tok'));
    expect(await screen.findByText(/industrial_fire/)).toBeTruthy();
    expect(st.refresh).toHaveBeenCalled();
  });

  it('says so when the server has decisions disabled, and shows server errors', async () => {
    render(<AlertInbox alerts={state({ decisionsEnabled: false })} open onClose={() => {}} onLocate={() => {}} />);
    expect(screen.getByTestId('decide-hint').textContent).toMatch(/ANALYST_TOKEN/);
    cleanup();
    post.mockRejectedValue(new Error('Invalid analyst token.'));
    render(<AlertInbox alerts={state()} open onClose={() => {}} onLocate={() => {}} />);
    fireEvent.change(screen.getByLabelText('Analyst token'), { target: { value: 'bad' } });
    fireEvent.click(screen.getByText('Reject'));
    expect(await screen.findByText('Invalid analyst token.')).toBeTruthy();
  });

  it('offers the PDF incident report one click from the alert', () => {
    render(<AlertInbox alerts={state()} open onClose={() => {}} onLocate={() => {}} />);
    const link = screen.getByTestId('report-link') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('/api/alerts/5/report.pdf');
    expect(link.hasAttribute('download')).toBe(true);
  });

  it('locates the source on the map and renders nothing when closed or unavailable', () => {
    const onLocate = vi.fn();
    const { rerender } = render(<AlertInbox alerts={state()} open onClose={() => {}} onLocate={onLocate} />);
    fireEvent.click(screen.getByText(/Show on map/));
    expect(onLocate).toHaveBeenCalledWith(expect.objectContaining({ source_id: 's1' }));
    rerender(<AlertInbox alerts={state()} open={false} onClose={() => {}} onLocate={onLocate} />);
    expect(screen.queryByLabelText('Alert inbox')).toBeNull();
    rerender(<AlertInbox alerts={state({ available: false, alerts: [] })} open onClose={() => {}} onLocate={onLocate} />);
    expect(screen.getByText(/need the PostGIS stack/)).toBeTruthy();
  });
});
