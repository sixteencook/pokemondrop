/** Structure générale : sidebar de navigation + topbar + contenu.
 *  Responsive : la sidebar devient une barre d'onglets en bas sur mobile. */

import { NavLink, Outlet } from "react-router-dom";
import {
  Activity,
  Bell,
  Boxes,
  Gauge,
  HeartPulse,
  LogOut,
  Package,
  Radar,
  ScrollText,
  Settings,
} from "lucide-react";
import { useAuth } from "@/auth/AuthProvider";
import { useWs } from "@/ws/WsProvider";
import { Badge } from "@/components/ui";

const NAV = [
  { to: "/", label: "Dashboard", icon: Gauge, end: true },
  { to: "/products", label: "Produits", icon: Package },
  { to: "/alerts", label: "Alertes", icon: Bell },
  { to: "/activity", label: "Activité", icon: Activity },
  { to: "/logs", label: "Logs", icon: ScrollText },
  { to: "/monitors", label: "Monitors", icon: Radar },
  { to: "/settings", label: "Paramètres", icon: Settings },
  { to: "/health", label: "Santé", icon: HeartPulse },
];

function ConnectionBadge() {
  const { status } = useWs();
  if (status === "open")
    return <Badge tone="success" dot pulse>Temps réel</Badge>;
  if (status === "connecting")
    return <Badge tone="warning" dot>Connexion…</Badge>;
  return <Badge tone="danger" dot>Hors ligne</Badge>;
}

export function Shell() {
  const { user, logout } = useAuth();

  return (
    <div className="flex min-h-dvh">
      {/* Sidebar (desktop) */}
      <aside className="fixed inset-y-0 hidden w-52 flex-col border-r border-border bg-surface md:flex">
        <div className="flex items-center gap-2 px-4 py-4">
          <Boxes size={20} className="text-accent-hover" />
          <span className="text-sm font-semibold tracking-tight">Drop Monitor</span>
        </div>
        <nav className="flex-1 space-y-0.5 px-2">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] font-medium
                 transition-colors duration-150 ${
                   isActive
                     ? "bg-surface-3 text-text"
                     : "text-muted hover:bg-surface-2 hover:text-text"
                 }`
              }
            >
              <Icon size={15} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-border px-4 py-3">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-xs text-muted">{user?.username}</span>
            <button
              onClick={() => void logout()}
              className="rounded-md p-1.5 text-muted transition-colors hover:bg-surface-2 hover:text-danger"
              title="Déconnexion"
            >
              <LogOut size={14} />
            </button>
          </div>
        </div>
      </aside>

      {/* Contenu */}
      <div className="flex min-w-0 flex-1 flex-col md:pl-52">
        <header className="sticky top-0 z-40 flex h-12 items-center justify-between border-b border-border bg-bg/80 px-4 backdrop-blur">
          <div className="flex items-center gap-2 md:hidden">
            <Boxes size={18} className="text-accent-hover" />
            <span className="text-sm font-semibold">Drop Monitor</span>
          </div>
          <div className="ml-auto">
            <ConnectionBadge />
          </div>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-5 pb-20 md:pb-5">
          <Outlet />
        </main>
      </div>

      {/* Barre d'onglets (mobile) */}
      <nav className="fixed inset-x-0 bottom-0 z-40 flex justify-around border-t border-border bg-surface/95 py-1.5 backdrop-blur md:hidden">
        {NAV.slice(0, 5).map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex flex-col items-center gap-0.5 rounded-md px-2 py-1 text-[10px]
               ${isActive ? "text-text" : "text-faint"}`
            }
          >
            <Icon size={17} />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
