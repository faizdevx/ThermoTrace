import type { ReactNode } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { LoginPage } from '@/components/LoginPage';

/** Shows the sign-in page until there is a session; the rest of the app is only mounted (and only calls the API) once signed in. */
export function AuthGate({ children }: { children: ReactNode }) {
  const auth = useAuth();
  if (auth.loading) return <p style={{ padding: 24, color: '#64748b' }}>Loading...</p>;
  if (auth.enforced && !auth.user) return <LoginPage />;
  return <>{children}</>;
}
