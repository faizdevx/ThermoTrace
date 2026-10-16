import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { fetchMe, login as apiLogin, logout as apiLogout } from '@/services/api';
import type { AuthMe, AuthUser, Role } from '@/types/industrial';

const RANK: Record<Role, number> = { viewer: 0, analyst: 1, admin: 2 };

export interface AuthState {
  loading: boolean;
  me: AuthMe | null;
  /** Sign-in is enforced (AUTH_MODE local or proxy). With AUTH_MODE=off everybody is anonymous and the legacy token rules apply. */
  enforced: boolean;
  user: AuthUser | null;
  /** True when the visitor may do what ``role`` allows; always true with AUTH_MODE=off (the server decides with its tokens). */
  can: (role: Role) => boolean;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const Ctx = createContext<AuthState>({ loading: false, me: null, enforced: false, user: null, can: () => true, signIn: async () => {}, signOut: async () => {} });

export function useAuth(): AuthState {
  return useContext(Ctx);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<AuthMe | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(() => {
    const controller = new AbortController();
    fetchMe(controller.signal).then(setMe).catch(() => setMe(null)).finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  useEffect(() => reload(), [reload]);
  useEffect(() => {                                      // any API call that answers 401 sends the visitor back to the sign-in page
    const on = () => setMe(m => (m ? { ...m, user: null } : m));
    window.addEventListener('tt-auth-required', on);
    return () => window.removeEventListener('tt-auth-required', on);
  }, []);

  const value = useMemo<AuthState>(() => {
    const enforced = !!me && me.auth_mode !== 'off';
    const user = me?.user ?? null;
    return {
      loading, me, enforced, user,
      can: (role: Role) => !enforced || (!!user && RANK[user.role] >= RANK[role]),
      signIn: async (username, password) => { const r = await apiLogin(username, password); setMe(m => (m ? { ...m, user: r.user } : m)); },
      signOut: async () => { await apiLogout().catch(() => undefined); setMe(m => (m ? { ...m, user: null } : m)); },
    };
  }, [me, loading]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
