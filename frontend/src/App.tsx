import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "@/auth/AuthProvider";
import { WsProvider, useWsEvent } from "@/ws/WsProvider";
import { ToastProvider, useToast } from "@/components/ui";
import { Shell } from "@/components/layout/Shell";
import { Spinner } from "@/components/ui";
import LoginPage from "@/pages/Login";
import DashboardPage from "@/pages/Dashboard";
import ProductsPage from "@/pages/Products";
import CatalogPage from "@/pages/Catalog";
import DiscoveryPage from "@/pages/Discovery";
import AlertsPage from "@/pages/Alerts";
import ActivityPage from "@/pages/Activity";
import LogsPage from "@/pages/Logs";
import MonitorsPage from "@/pages/Monitors";
import SettingsPage from "@/pages/Settings";
import HealthPage from "@/pages/Health";

/** Toasts temps réel : une alerte détectée par le moteur apparaît
 *  instantanément, avant même l'envoi Telegram. */
function RealtimeToasts() {
  const { push } = useToast();
  useWsEvent("alert", (message) => {
    const payload = message.payload as {
      label?: string;
      product?: { name?: string };
      price?: string | null;
    };
    push({
      tone: "success",
      title: payload.label ?? "Changement détecté",
      description: [payload.product?.name, payload.price]
        .filter(Boolean)
        .join(" — "),
    });
  });

  useWsEvent("discovery", (message) => {
    const payload = message.payload as {
      title?: string;
      site_label?: string;
      imported?: boolean;
    };
    push({
      tone: "accent",
      title: "🆕 Nouveau produit détecté",
      description: [
        payload.site_label,
        payload.title,
        payload.imported ? "surveillance démarrée" : "à valider",
      ]
        .filter(Boolean)
        .join(" — "),
    });
  });

  return null;
}

function Protected() {
  const { state } = useAuth();
  if (state === "loading") return <Spinner label="Vérification de la session…" />;
  if (state === "anonymous") return <Navigate to="/login" replace />;
  return (
    <WsProvider>
      <RealtimeToasts />
      <Shell />
    </WsProvider>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<Protected />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/products" element={<ProductsPage />} />
            <Route path="/catalog" element={<CatalogPage />} />
            <Route path="/discovery" element={<DiscoveryPage />} />
            <Route path="/alerts" element={<AlertsPage />} />
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="/monitors" element={<MonitorsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/health" element={<HealthPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </AuthProvider>
    </ToastProvider>
  );
}
