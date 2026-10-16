import { useState, type FormEvent } from 'react';
import { useAuth } from '@/hooks/useAuth';

export function LoginPage() {
  const auth = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await auth.signIn(username, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed');
    } finally {
      setBusy(false);
    }
  };

  const proxy = auth.me?.auth_mode === 'proxy';
  const disabled = busy || !username || !password;
  const field: React.CSSProperties = { width: '100%', boxSizing: 'border-box', padding: '10px 12px', borderRadius: 10, border: '1px solid #cbd5e1', fontSize: 15, marginBottom: 12 };
  return (
    <main style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: 'linear-gradient(180deg, #f0f7ff 0%, #e8f0fe 100%)', padding: 16 }}>
      <form onSubmit={submit} aria-label="Sign in" style={{ width: 'min(380px, 100%)', background: '#fff', borderRadius: 18, padding: '28px 26px', boxShadow: '0 8px 32px rgba(15,23,42,0.14)' }}>
        <p style={{ margin: 0, fontSize: 10, fontWeight: 700, letterSpacing: '0.22em', textTransform: 'uppercase', color: '#0ea5e9' }}>India Industrial Intelligence</p>
        <h1 style={{ margin: '4px 0 18px', fontSize: 24, color: '#0f172a' }}>ThermoTrace</h1>
        {proxy ? (
          <p data-testid="proxy-hint" style={{ fontSize: 14, color: '#475569' }}>
            Your organisation's sign-in did not reach this page. Open it through the single sign-on address, or ask an administrator to add you to a ThermoTrace group.
          </p>
        ) : (
          <>
            <label htmlFor="login-user" style={{ fontSize: 12, fontWeight: 600, color: '#475569' }}>Username</label>
            <input id="login-user" autoComplete="username" autoFocus value={username} onChange={e => setUsername(e.target.value)} style={field} />
            <label htmlFor="login-pass" style={{ fontSize: 12, fontWeight: 600, color: '#475569' }}>Password</label>
            <input id="login-pass" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} style={field} />
            {error && <p role="alert" style={{ margin: '0 0 12px', color: '#b91c1c', fontSize: 13 }}>{error}</p>}
            <button type="submit" disabled={disabled}
              style={{ width: '100%', padding: '11px 0', borderRadius: 10, border: 'none', background: disabled ? '#94a3b8' : '#0f172a', color: '#fff', fontWeight: 700, fontSize: 15, cursor: 'pointer' }}>
              {busy ? 'Signing in...' : 'Sign in'}
            </button>
          </>
        )}
        <p style={{ margin: '16px 0 0', fontSize: 11, color: '#94a3b8' }}>Access is logged. Thermal detections are evidence, not confirmed fires.</p>
      </form>
    </main>
  );
}
