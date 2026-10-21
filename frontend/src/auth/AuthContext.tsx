import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { Me } from '../api/types';
import { useI18n } from '../i18n';

type State = { status: 'loading' } | { status: 'anon' } | { status: 'user'; me: Me };
interface Ctx {
  state: State;
  me: Me | null;
  refresh: () => Promise<Me | null>;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, invite?: string) => Promise<void>;
  logout: () => Promise<void>;
}
const AuthCtx = createContext<Ctx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ status: 'loading' });
  const { setLang } = useI18n();

  const refresh = useCallback(async (): Promise<Me | null> => {
    try {
      const sess = await api.session();            // always 200, so an anonymous visit prints nothing to the console
      if (!sess.authenticated) { setState({ status: 'anon' }); return null; }
      const me = await api.me();
      setState({ status: 'user', me });
      if (me.language === 'en' || me.language === 'fa') setLang(me.language);
      return me;
    } catch {
      setState({ status: 'anon' });
      return null;
    }
  }, [setLang]);

  useEffect(() => { void refresh(); }, [refresh]);

  const value = useMemo<Ctx>(() => ({
    state,
    me: state.status === 'user' ? state.me : null,
    refresh,
    login: async (username, password) => { await api.login({ username, password }); await refresh(); },
    register: async (username, password, invite) => { await api.register({ username, password, invite: invite ?? '' }); await refresh(); },
    logout: async () => { try { await api.logout(); } finally { setState({ status: 'anon' }); } },
  }), [state, refresh]);
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): Ctx { const c = useContext(AuthCtx); if (!c) throw new Error('useAuth outside AuthProvider'); return c; }
