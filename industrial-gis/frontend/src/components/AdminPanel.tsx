import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { createUser, fetchAudit, fetchUsers, updateUser, verifyAudit } from '@/services/api';
import type { AuditEntry, AuthUser, Role } from '@/types/industrial';

const card: React.CSSProperties = { background: 'rgba(255,255,255,0.92)', borderRadius: 14, padding: '14px 16px', boxShadow: '0 1px 4px rgba(15,23,42,0.08)' };
const h: React.CSSProperties = { margin: '0 0 10px', fontSize: 12, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase', color: '#64748b' };
const input: React.CSSProperties = { padding: '6px 8px', borderRadius: 8, border: '1px solid #cbd5e1', fontSize: 13 };
const ROLES: Role[] = ['viewer', 'analyst', 'admin'];
const ACTIONS = ['', 'login', 'alert.decision', 'export', 'user', 'access.denied', 'admin'];

export function summariseDetail(e: AuditEntry): string {
  if (!e.detail) return '';
  const d = e.detail as Record<string, unknown>;
  if (e.action === 'alert.decision') return `${d.decision}${d.note ? ` - ${String(d.note).slice(0, 80)}` : ''}`;
  if (e.action.startsWith('export.')) return `${d.format ?? ''} ${d.rows != null ? `${d.rows} rows` : ''} ${d.filters ? JSON.stringify(d.filters) : ''}`.trim();
  return JSON.stringify(d).slice(0, 120);
}

function Users({ me }: { me: AuthUser | null }) {
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ username: '', display_name: '', role: 'viewer' as Role, password: '' });
  const load = useCallback(() => { fetchUsers().then(r => { setUsers(r.users); setError(null); }).catch(e => setError(e instanceof Error ? e.message : 'failed')); }, []);
  useEffect(load, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); setError(null); load(); } catch (e) { setError(e instanceof Error ? e.message : 'failed'); }
  };
  const create = (ev: FormEvent) => { ev.preventDefault(); act(async () => { await createUser(form); setForm({ ...form, username: '', display_name: '', password: '' }); }); };

  return (
    <div style={card} data-testid="admin-users">
      <p style={h}>Accounts</p>
      {error && <p role="alert" style={{ color: '#b91c1c', fontSize: 13, margin: '0 0 8px' }}>{error}</p>}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr style={{ textAlign: 'left', color: '#64748b' }}><th>User</th><th>Role</th><th>Status</th><th>Last sign-in</th><th /></tr></thead>
          <tbody>
            {users.map(u => (
              <tr key={u.username} style={{ borderTop: '1px solid #f1f5f9' }}>
                <td style={{ padding: '6px 4px' }}><strong>{u.username}</strong>{u.display_name ? <span style={{ color: '#64748b' }}> {u.display_name}</span> : null}</td>
                <td>
                  <select aria-label={`Role of ${u.username}`} value={u.role} disabled={u.username === me?.username} style={input}
                    onChange={e => act(() => updateUser(u.user_id!, { role: e.target.value }))}>
                    {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                </td>
                <td style={{ color: u.active ? '#166534' : '#b91c1c' }}>{u.active ? 'active' : 'disabled'}</td>
                <td style={{ color: '#64748b' }}>{u.last_login_at ? new Date(u.last_login_at).toLocaleString() : 'never'}</td>
                <td style={{ textAlign: 'right' }}>
                  {u.username !== me?.username && (
                    <button onClick={() => act(() => updateUser(u.user_id!, { active: !u.active }))} style={{ ...input, cursor: 'pointer' }}>{u.active ? 'Disable' : 'Enable'}</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <form onSubmit={create} style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12, alignItems: 'center' }} aria-label="Create account">
        <input aria-label="New username" placeholder="username" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} style={input} />
        <input aria-label="New display name" placeholder="name" value={form.display_name} onChange={e => setForm({ ...form, display_name: e.target.value })} style={input} />
        <select aria-label="New role" value={form.role} onChange={e => setForm({ ...form, role: e.target.value as Role })} style={input}>{ROLES.map(r => <option key={r}>{r}</option>)}</select>
        <input aria-label="Initial password" type="password" placeholder="initial password (12+)" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} style={input} />
        <button type="submit" disabled={!form.username || !form.password} style={{ ...input, background: '#0f172a', color: '#fff', cursor: 'pointer', fontWeight: 600 }}>Create</button>
      </form>
    </div>
  );
}

function Audit() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [action, setAction] = useState('');
  const [verdict, setVerdict] = useState<string | null>(null);
  const load = useCallback(() => { fetchAudit({ limit: 60, action: action || undefined }).then(r => setEntries(r.entries)).catch(() => setEntries([])); }, [action]);
  useEffect(load, [load]);
  const verify = async () => {
    const r = await verifyAudit();
    setVerdict(r.ok ? `Chain intact: ${r.rows} entries, head ${r.head?.slice(0, 16) ?? 'n/a'}...` : `CHAIN BROKEN at entry ${r.first_bad_id} (${r.rows} entries read)`);
  };
  return (
    <div style={card} data-testid="admin-audit">
      <p style={h}>Audit log (append-only, hash-chained)</p>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
        <select aria-label="Filter by action" value={action} onChange={e => setAction(e.target.value)} style={input}>
          {ACTIONS.map(a => <option key={a} value={a}>{a || 'all actions'}</option>)}
        </select>
        <button onClick={load} style={{ ...input, cursor: 'pointer' }}>Refresh</button>
        <button onClick={verify} style={{ ...input, cursor: 'pointer' }}>Verify chain</button>
        {verdict && <span role="status" data-testid="audit-verdict" style={{ alignSelf: 'center', fontSize: 12, color: verdict.startsWith('CHAIN BROKEN') ? '#b91c1c' : '#166534' }}>{verdict}</span>}
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead><tr style={{ textAlign: 'left', color: '#64748b' }}><th>When (UTC)</th><th>Who</th><th>Action</th><th>Target</th><th>Detail</th><th>From</th></tr></thead>
          <tbody>
            {entries.map(e => (
              <tr key={e.audit_id} style={{ borderTop: '1px solid #f1f5f9' }}>
                <td style={{ padding: '4px', whiteSpace: 'nowrap' }}>{e.at.replace('T', ' ').slice(0, 19)}</td>
                <td>{e.actor}{e.role ? <span style={{ color: '#64748b' }}> ({e.role})</span> : null}</td>
                <td><code>{e.action}</code></td>
                <td>{e.target ?? ''}</td>
                <td style={{ maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={JSON.stringify(e.detail)}>{summariseDetail(e)}</td>
                <td style={{ color: '#64748b' }}>{e.ip ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {entries.length === 0 && <p style={{ color: '#64748b', fontSize: 13 }}>No entries match.</p>}
      </div>
    </div>
  );
}

export function AdminPanel({ me, localAccounts }: { me: AuthUser | null; localAccounts: boolean }) {
  return (
    <div style={{ display: 'grid', gap: 12 }} data-testid="admin-panel">
      {localAccounts ? <Users me={me} /> : <div style={card}><p style={h}>Accounts</p><p style={{ fontSize: 13, color: '#475569' }}>Accounts and groups are managed by the identity provider behind the proxy.</p></div>}
      <Audit />
    </div>
  );
}
