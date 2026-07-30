/** Page d'accueil : état global, graphiques, cartes produits, activité. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlarmClock,
  Bell,
  Boxes,
  Gauge,
  Globe,
  PackageSearch,
  Timer,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { productsApi, statsApi, timelineApi } from "@/api/endpoints";
import type { Product, ProductInput } from "@/api/types";
import {
  Badge,
  ChartCard,
  EmptyState,
  PageHeader,
  Spinner,
  StatCard,
  TimelineList,
  useToast,
} from "@/components/ui";
import { CHART_COLORS } from "@/components/ui/ChartCard";
import { ProductCard } from "@/components/domain/ProductCard";
import { ProductFormModal } from "@/components/domain/ProductFormModal";
import { ProductTimelineModal } from "@/components/domain/ProductTimelineModal";
import { useQuery as useMonitorsQuery } from "@tanstack/react-query";
import { monitorsApi } from "@/api/endpoints";
import {
  EVENT_TYPE_META,
  formatDateTime,
  formatDuration,
  formatMs,
  formatTimeAgo,
} from "@/lib/format";
import { ApiError } from "@/api/client";

const CHART_TOOLTIP = {
  contentStyle: {
    background: "#17171a",
    border: "1px solid #26262b",
    borderRadius: 8,
    fontSize: 12,
  },
  labelStyle: { color: "#8b8b93" },
};

export default function DashboardPage() {
  const queryClient = useQueryClient();
  const { push } = useToast();
  const [editing, setEditing] = useState<Product | null>(null);
  const [historyOf, setHistoryOf] = useState<Product | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const stats = useQuery({ queryKey: ["stats", "overview"], queryFn: statsApi.overview });
  const products = useQuery({
    queryKey: ["products", "dashboard"],
    queryFn: () => productsApi.list({ page_size: 50, sort: "priority", order: "desc" }),
  });
  const activity = useQuery({
    queryKey: ["timeline", "recent"],
    queryFn: () => timelineApi.list({ page_size: 8 }),
  });
  const checksPerHour = useQuery({
    queryKey: ["stats", "checks-per-hour"],
    queryFn: () => statsApi.checksPerHour(24),
  });
  const alertsPerDay = useQuery({
    queryKey: ["stats", "alerts-per-day"],
    queryFn: () => statsApi.alertsPerDay(14),
  });
  const monitors = useMonitorsQuery({ queryKey: ["monitors"], queryFn: monitorsApi.list });

  const invalidateProducts = () => {
    void queryClient.invalidateQueries({ queryKey: ["products"] });
    void queryClient.invalidateQueries({ queryKey: ["stats"] });
  };

  const toggle = useMutation({
    mutationFn: (product: Product) =>
      productsApi.update(product.uuid, { enabled: !product.enabled }),
    onSuccess: invalidateProducts,
  });

  const checkNow = useMutation({
    mutationFn: (product: Product) => productsApi.checkNow(product.uuid),
    onSuccess: (result, product) => {
      invalidateProducts();
      push({
        tone: result.status === "ok" ? "info" : "danger",
        title: result.status === "ok" ? "Vérification effectuée" : "Vérification en échec",
        description: product.name,
      });
    },
    onError: (error, product) =>
      push({
        tone: "danger",
        title: "Vérification impossible",
        description: error instanceof ApiError ? error.message : product.name,
      }),
  });

  const save = useMutation({
    mutationFn: (values: ProductInput) => productsApi.update(editing!.uuid, values),
    onSuccess: () => {
      setEditing(null);
      setFormError(null);
      invalidateProducts();
    },
    onError: (error) =>
      setFormError(error instanceof ApiError ? error.message : "Enregistrement impossible."),
  });

  const overview = stats.data;

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Vue d'ensemble de la surveillance en temps réel."
        actions={
          overview && (
            <Badge tone={overview.monitor_active ? "success" : "neutral"} dot
                   pulse={overview.monitor_active}>
              {overview.monitor_active
                ? `Monitor actif — ${overview.products_watched} produit(s)`
                : "Monitor en veille"}
            </Badge>
          )
        }
      />

      {/* État global */}
      {overview ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <StatCard label="Produits surveillés" tone="accent" icon={<Boxes size={16} />}
                    value={`${overview.products_watched} / ${overview.products_total}`}
                    hint={`${overview.products_enabled} activé(s)`} />
          <StatCard label="Sites" tone="info" icon={<Globe size={16} />}
                    value={overview.sites_count}
                    hint={`${monitors.data?.length ?? "…"} plugin(s) chargé(s)`} />
          <StatCard label="Dernière vérification" tone="neutral" icon={<AlarmClock size={16} />}
                    value={formatTimeAgo(overview.last_check_at)} />
          <StatCard label="Dernière alerte" tone="success" icon={<Bell size={16} />}
                    value={formatTimeAgo(overview.last_alert_at)} />
          <StatCard label="Fonctionnement" tone="neutral" icon={<Activity size={16} />}
                    value={formatDuration(overview.uptime_seconds)} />
          <StatCard label="Checks effectués" tone="info" icon={<PackageSearch size={16} />}
                    value={overview.checks_total.toLocaleString("fr-FR")} />
          <StatCard label="Alertes envoyées" tone="warning" icon={<Bell size={16} />}
                    value={overview.alerts_total} />
          <StatCard label="Temps de réponse moyen" tone="accent" icon={<Timer size={16} />}
                    value={formatMs(overview.avg_response_ms_24h)} hint="24 dernières heures" />
        </div>
      ) : (
        <Spinner />
      )}

      {/* Graphiques */}
      <div className="mt-5 grid gap-4 lg:grid-cols-2">
        <ChartCard title="Checks par heure (24 h)"
                   isEmpty={(checksPerHour.data?.length ?? 0) === 0}>
          <BarChart data={checksPerHour.data ?? []}>
            <CartesianGrid stroke={CHART_COLORS.grid} vertical={false} />
            <XAxis dataKey="hour" tickFormatter={(hour: string) => hour.slice(11)}
                   stroke={CHART_COLORS.text} fontSize={11} tickLine={false} />
            <YAxis stroke={CHART_COLORS.text} fontSize={11} tickLine={false} width={32} />
            <Tooltip {...CHART_TOOLTIP} />
            <Bar dataKey="total" name="Checks" fill={CHART_COLORS.accent}
                 radius={[3, 3, 0, 0]} />
            <Bar dataKey="errors" name="Erreurs" fill={CHART_COLORS.danger}
                 radius={[3, 3, 0, 0]} />
          </BarChart>
        </ChartCard>
        <ChartCard title="Alertes par jour (14 j)"
                   isEmpty={(alertsPerDay.data?.length ?? 0) === 0}>
          <AreaChart data={alertsPerDay.data ?? []}>
            <CartesianGrid stroke={CHART_COLORS.grid} vertical={false} />
            <XAxis dataKey="day" tickFormatter={(day: string) => day.slice(5)}
                   stroke={CHART_COLORS.text} fontSize={11} tickLine={false} />
            <YAxis stroke={CHART_COLORS.text} fontSize={11} tickLine={false}
                   width={32} allowDecimals={false} />
            <Tooltip {...CHART_TOOLTIP} />
            <Area dataKey="total" name="Alertes" stroke={CHART_COLORS.success}
                  fill={CHART_COLORS.success} fillOpacity={0.15} strokeWidth={2} />
          </AreaChart>
        </ChartCard>
      </div>

      {/* Cartes produits */}
      <h2 className="mb-3 mt-6 text-sm font-medium text-text">Produits</h2>
      {products.isLoading && <Spinner />}
      {products.data && products.data.items.length === 0 && (
        <EmptyState icon={<Gauge size={24} />} title="Aucun produit"
                    description="Ajoutez vos produits depuis la page Produits." />
      )}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {products.data?.items.map((product) => (
          <ProductCard
            key={product.uuid}
            product={product}
            busy={toggle.isPending || checkNow.isPending}
            onToggle={(p) => toggle.mutate(p)}
            onEdit={(p) => { setFormError(null); setEditing(p); }}
            onCheckNow={(p) => checkNow.mutate(p)}
            onHistory={setHistoryOf}
          />
        ))}
      </div>

      {/* Activité récente */}
      <h2 className="mb-3 mt-6 text-sm font-medium text-text">Activité récente</h2>
      {activity.data && activity.data.items.length > 0 ? (
        <div className="rounded-lg border border-border bg-surface p-4">
          <TimelineList
            items={activity.data.items.map((entry) => ({
              id: entry.id,
              label: entry.label,
              time: formatDateTime(entry.created_at),
              tone: EVENT_TYPE_META[entry.event_type]?.tone ?? "neutral",
              meta: entry.new_value ?? undefined,
            }))}
          />
        </div>
      ) : (
        <EmptyState icon={<Activity size={22} />} title="Aucune activité pour le moment"
                    description="Les événements apparaîtront ici en temps réel." />
      )}

      <ProductFormModal
        open={editing !== null}
        product={editing}
        sites={monitors.data?.map((monitor) => monitor.site) ?? ["micromania"]}
        saving={save.isPending}
        error={formError}
        onClose={() => setEditing(null)}
        onSubmit={(values) => save.mutate(values)}
      />
      <ProductTimelineModal product={historyOf} onClose={() => setHistoryOf(null)} />
    </>
  );
}
