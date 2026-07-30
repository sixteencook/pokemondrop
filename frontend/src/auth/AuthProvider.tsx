/** Session utilisateur : vérifie la session au boot, gère login/logout,
 *  réagit à l'expiration (événement global `auth:expired` émis par le client
 *  HTTP sur toute réponse 401). */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { authApi } from "@/api/endpoints";
import type { User } from "@/api/types";

type AuthState = "loading" | "authenticated" | "anonymous";

interface AuthContextValue {
  state: AuthState;
  user: User | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>("loading");
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    authApi
      .me()
      .then((me) => {
        setUser(me);
        setState("authenticated");
      })
      .catch(() => setState("anonymous"));
  }, []);

  useEffect(() => {
    const onExpired = () => {
      setUser(null);
      setState("anonymous");
    };
    window.addEventListener("auth:expired", onExpired);
    return () => window.removeEventListener("auth:expired", onExpired);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const me = await authApi.login(username, password);
    setUser(me);
    setState("authenticated");
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      setUser(null);
      setState("anonymous");
    }
  }, []);

  return (
    <AuthContext.Provider value={{ state, user, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth doit être utilisé sous <AuthProvider>");
  return value;
}
