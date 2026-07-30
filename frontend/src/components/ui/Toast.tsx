/** Notifications éphémères (toasts) — générique, sans logique métier. */

import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Bell, CheckCircle2, Info, X } from "lucide-react";
import type { Tone } from "@/lib/format";

interface Toast {
  id: number;
  tone: Tone;
  title: string;
  description?: string;
}

interface ToastContextValue {
  push: (toast: Omit<Toast, "id">) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const TONE_ICONS: Record<Tone, ReactNode> = {
  success: <CheckCircle2 size={16} className="text-success" />,
  warning: <AlertTriangle size={16} className="text-warning" />,
  danger: <AlertTriangle size={16} className="text-danger" />,
  info: <Info size={16} className="text-info" />,
  accent: <Bell size={16} className="text-accent-hover" />,
  neutral: <Info size={16} className="text-muted" />,
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counter = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback(
    (toast: Omit<Toast, "id">) => {
      const id = ++counter.current;
      setToasts((current) => [...current.slice(-4), { ...toast, id }]);
      window.setTimeout(() => dismiss(id), 6000);
    },
    [dismiss]
  );

  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      {createPortal(
        <div className="fixed bottom-4 right-4 z-[60] flex w-80 flex-col gap-2">
          {toasts.map((toast) => (
            <div
              key={toast.id}
              className="flex items-start gap-2.5 rounded-lg border border-border bg-surface-2 p-3 shadow-xl animate-fade-up"
            >
              <div className="mt-0.5 shrink-0">{TONE_ICONS[toast.tone]}</div>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-text">{toast.title}</p>
                {toast.description && (
                  <p className="mt-0.5 text-xs text-muted">{toast.description}</p>
                )}
              </div>
              <button
                onClick={() => dismiss(toast.id)}
                className="shrink-0 text-faint transition-colors hover:text-text"
                aria-label="Fermer"
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>,
        document.body
      )}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const value = useContext(ToastContext);
  if (!value) throw new Error("useToast doit être utilisé sous <ToastProvider>");
  return value;
}
