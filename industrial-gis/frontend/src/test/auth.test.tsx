import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AuthGate } from '@/components/AuthGate';
import { AdminPanel, summariseDetail } from '@/components/AdminPanel';
import { AlertInbox } from '@/components/AlertInbox';
import { AuthProvider } from '@/hooks/useAuth';
import type { AlertsState } from '@/hooks/useAlerts';
import type { AuthMe, AuthUser, ThermalAlert } from '@/types/industrial';

const api = vi.hoisted(() => ({
  fetchMe: vi.fn(), login: vi.fn(), logout: vi.fn(), postAlertDecision: vi.fn(),
  fetchUsers: vi.fn(), createUser: vi.fn(), updateUser: vi.fn(), fetchAudit: vi.fn(), verifyAudit: vi.fn(),
}));
vi.mock('@/services/api', () => ({ API_BASE: '/api', ...api }));

const user = (role: AuthUser['role'], name = 'asha'): AuthUser => ({ user_id: 1, username: name, display_name: null, role, active: true });
const me = (over: Partial<AuthMe> = {}): AuthMe => ({ auth_mode: 'local', user: null, demo_readonly: false, offline_mode: false, session_idle_minutes: 60, ...over });

const alert = {
  alert_id: 5, facility_key: 'f', source_id: 's1', site_ref: 'osm:1', site_name: 'Plant A', site_kind: 'steel_plant', state: 'Odisha', trigger: 'p_fire', severity: 'high',
  p_fire: 0.7, frp_max: 60, frp_z: null, first_seen: null, last_seen: null, peak_frp: 60, peak_at: null, summary: 'HIGH: x', reasons: null, imagery: null, status: 'open',
  delivery: {}, is_test: false, created_at: '2026-10-16T03:05:00Z', updated_at: '2026-10-16T03:05:00Z', lat: 20, lon: 85, timeline: [],
} as ThermalAlert;
const inbox = (): AlertsState => ({ alerts: [alert], available: true, decisionsEnabled: true, demoReadonly: false, channels: [], error: null, selectedId: 5, select: vi.fn(), detail: alert, refresh: vi.fn() }) as AlertsState;

beforeEach(() => { Object.values(api).forEach(f => f.mockReset()); });
afterEach(cleanup);

describe('AuthGate', () => {
  it('shows the sign-in page until a session exists, then the app', async () => {
    api.fetchMe.mockResolvedValue(me());
    api.login.mockResolvedValue({ user: user('viewer') });
    render(<AuthProvider><AuthGate><p>the app</p></AuthGate></AuthProvider>);
    expect(await screen.findByLabelText('Sign in')).toBeTruthy();
    expect(screen.queryByText('the app')).toBeNull();
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'vik' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'Correct-Horse-Battery-9' } });
    fireEvent.click(screen.getByText('Sign in'));
    await waitFor(() => expect(api.login).toHaveBeenCalledWith('vik', 'Correct-Horse-Battery-9'));
    expect(await screen.findByText('the app')).toBeTruthy();
  });

  it('shows the server message when sign-in fails and does not open the app', async () => {
    api.fetchMe.mockResolvedValue(me());
    api.login.mockRejectedValue(new Error('Wrong username or password.'));
    render(<AuthProvider><AuthGate><p>the app</p></AuthGate></AuthProvider>);
    fireEvent.change(await screen.findByLabelText('Username'), { target: { value: 'vik' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'nope-nope-nope-1' } });
    fireEvent.click(screen.getByText('Sign in'));
    expect(await screen.findByText('Wrong username or password.')).toBeTruthy();
    expect(screen.queryByText('the app')).toBeNull();
  });

  it('lets everybody in when authentication is off, and explains the proxy mode', async () => {
    api.fetchMe.mockResolvedValue(me({ auth_mode: 'off' }));
    render(<AuthProvider><AuthGate><p>the app</p></AuthGate></AuthProvider>);
    expect(await screen.findByText('the app')).toBeTruthy();
    cleanup();
    api.fetchMe.mockResolvedValue(me({ auth_mode: 'proxy' }));
    render(<AuthProvider><AuthGate><p>the app</p></AuthGate></AuthProvider>);
    expect((await screen.findByTestId('proxy-hint')).textContent).toMatch(/single sign-on/);
  });

  it('goes back to the sign-in page when any request answers 401', async () => {
    api.fetchMe.mockResolvedValue(me({ user: user('analyst') }));
    render(<AuthProvider><AuthGate><p>the app</p></AuthGate></AuthProvider>);
    expect(await screen.findByText('the app')).toBeTruthy();
    window.dispatchEvent(new Event('tt-auth-required'));
    expect(await screen.findByLabelText('Sign in')).toBeTruthy();
  });
});

describe('role-aware inbox', () => {
  it('a viewer sees the alert but cannot decide or download the report', async () => {
    api.fetchMe.mockResolvedValue(me({ user: user('viewer', 'vik') }));
    render(<AuthProvider><AlertInbox alerts={inbox()} open onClose={() => {}} onLocate={() => {}} /></AuthProvider>);
    expect((await screen.findByTestId('role-hint')).textContent).toMatch(/needs the analyst role/);
    expect(screen.queryByText('Confirm fire')).toBeNull();
    expect(screen.queryByTestId('report-link')).toBeNull();
  });

  it('an analyst decides as the signed-in user without any token field', async () => {
    api.fetchMe.mockResolvedValue(me({ user: user('analyst', 'asha') }));
    api.postAlertDecision.mockResolvedValue({ alert_id: 5, decision: 'rejected', label_id: 'analyst-alert-5', label: 'not_industrial_fire' });
    render(<AuthProvider><AlertInbox alerts={inbox()} open onClose={() => {}} onLocate={() => {}} /></AuthProvider>);
    expect((await screen.findByTestId('signed-in-as')).textContent).toMatch(/asha/);
    expect(screen.getByTestId('report-link')).toBeTruthy();
    const reject = screen.getByText('Reject') as HTMLButtonElement;
    expect(reject.disabled).toBe(false);
    fireEvent.click(reject);
    await waitFor(() => expect(api.postAlertDecision).toHaveBeenCalledWith(5, 'rejected', '', 'asha', ''));
  });
});

describe('AdminPanel', () => {
  it('lists accounts, creates one and reports a broken audit chain', async () => {
    api.fetchUsers.mockResolvedValue({ users: [user('admin', 'root'), { ...user('viewer', 'vik'), user_id: 2, last_login_at: null }] });
    api.fetchAudit.mockResolvedValue({ entries: [{ audit_id: 9, at: '2026-10-16T05:00:00.000000+00:00', actor: 'asha', role: 'analyst', action: 'alert.decision', target: '5',
      detail: { decision: 'confirmed', note: 'smoke' }, ip: '10.0.0.2', row_hash: 'abc' }] });
    api.createUser.mockResolvedValue({ user: user('analyst', 'newbie') });
    api.verifyAudit.mockResolvedValue({ ok: false, rows: 120, first_bad_id: 44, head: null });
    render(<AdminPanel me={user('admin', 'root')} localAccounts />);
    expect(await screen.findByText('vik')).toBeTruthy();
    expect(screen.getByText('confirmed - smoke')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('New username'), { target: { value: 'newbie' } });
    fireEvent.change(screen.getByLabelText('Initial password'), { target: { value: 'Correct-Horse-Battery-9' } });
    fireEvent.click(screen.getByText('Create'));
    await waitFor(() => expect(api.createUser).toHaveBeenCalledWith(expect.objectContaining({ username: 'newbie', role: 'viewer' })));
    fireEvent.click(screen.getByText('Verify chain'));
    expect((await screen.findByTestId('audit-verdict')).textContent).toBe('CHAIN BROKEN at entry 44 (120 entries read)');
  });

  it('summarises exports and decisions for the table', () => {
    expect(summariseDetail({ audit_id: 1, at: 'x', actor: 'a', role: null, action: 'export.thermal', target: null, detail: { format: 'csv', rows: 12, filters: { state: 'Odisha' } }, ip: null, row_hash: '' }))
      .toBe('csv 12 rows {"state":"Odisha"}');
  });
});
