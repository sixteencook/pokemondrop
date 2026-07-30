/** Connexion WebSocket unique de l'application.
 *
 * - reconnexion automatique avec backoff exponentiel (1 s → 30 s) ;
 * - abonnement par type de message : `subscribe("alert", cb)` ;
 * - invalidation des caches React Query sur les événements temps réel
 *   (throttlée pour les checks, immédiate pour les alertes).
 *
 * Composant découplé : aucun composant UI ne crée de socket, tous passent
 * par `useWs()` / `useWsEvent()`.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { WsMessage } from "@/api/types";

export type WsStatus = "connecting" | "open" | "closed";

type Listener = (message: WsMessage) => void;

interface WsContextValue {
  status: WsStatus;
  subscribe: (type: string, listener: Listener) => () => void;
}

const WsContext = createContext<WsContextValue | null>(null);

export function WsProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<WsStatus>("connecting");
  const listenersRef = useRef<Map<string, Set<Listener>>>(new Map());
  const queryClient = useQueryClient();
  const lastCheckInvalidation = useRef(0);

  const subscribe = useCallback((type: string, listener: Listener) => {
    const map = listenersRef.current;
    if (!map.has(type)) map.set(type, new Set());
    map.get(type)!.add(listener);
    return () => map.get(type)?.delete(listener);
  }, []);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let closed = false;
    let attempt = 0;
    let reconnectTimer: number | undefined;

    const dispatch = (message: WsMessage) => {
      listenersRef.current.get(message.type)?.forEach((cb) => cb(message));
      listenersRef.current.get("*")?.forEach((cb) => cb(message));

      // Invalidation des caches : le dashboard reste juste sans polling.
      if (message.type === "check") {
        const now = Date.now();
        if (now - lastCheckInvalidation.current > 3000) {
          lastCheckInvalidation.current = now;
          queryClient.invalidateQueries({ queryKey: ["products"] });
          queryClient.invalidateQueries({ queryKey: ["stats"] });
          queryClient.invalidateQueries({ queryKey: ["checks"] });
        }
      } else if (message.type === "alert" || message.type === "alert_status") {
        queryClient.invalidateQueries({ queryKey: ["alerts"] });
        queryClient.invalidateQueries({ queryKey: ["stats"] });
      } else if (message.type === "timeline") {
        queryClient.invalidateQueries({ queryKey: ["timeline"] });
      }
    };

    const connect = () => {
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      setStatus("connecting");
      socket = new WebSocket(`${scheme}://${location.host}/api/v1/ws`);

      socket.onopen = () => {
        attempt = 0;
        setStatus("open");
      };
      socket.onmessage = (event) => {
        try {
          dispatch(JSON.parse(event.data) as WsMessage);
        } catch {
          /* message illisible : ignoré */
        }
      };
      socket.onclose = () => {
        setStatus("closed");
        if (closed) return;
        attempt += 1;
        const delay = Math.min(30_000, 1000 * 2 ** Math.min(attempt, 5));
        reconnectTimer = window.setTimeout(connect, delay);
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      closed = true;
      window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [queryClient]);

  return (
    <WsContext.Provider value={{ status, subscribe }}>
      {children}
    </WsContext.Provider>
  );
}

export function useWs(): WsContextValue {
  const value = useContext(WsContext);
  if (!value) throw new Error("useWs doit être utilisé sous <WsProvider>");
  return value;
}

/** Abonnement déclaratif à un type de message. */
export function useWsEvent(type: string, listener: Listener): void {
  const { subscribe } = useWs();
  const ref = useRef(listener);
  ref.current = listener;
  useEffect(() => subscribe(type, (msg) => ref.current(msg)), [subscribe, type]);
}
