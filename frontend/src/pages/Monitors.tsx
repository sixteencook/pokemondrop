/** Plugins de sites : identité, agrégats, répartition des produits. */

import { useQuery } from "@tanstack/react-query";
import { Cell, Pie, PieChart, Tooltip } from "recharts";
import { ExternalLink, Radar } from "lucide-react";
import { monitorsApi, statsApi } from "@/api/endpoints";
import { Badge, Card, ChartCard, EmptyState, PageHeader, Spinner } from "@/components/ui";
import { CHART_COLORS } from "@/components/ui/ChartCard";
import { formatMs, formatTimeAgo } from "@/lib/format";

const PIE_COLORS = [
  CHART_COLORS.accent,
  CHART_COLORS.info,
  CHART_COLORS.success,
  CHART_COLORS.warning,
  CHART_COLORS.danger,
];

export default function MonitorsPage() {
  const monitors = useQuery({ queryKey: ["monitors"], queryFn: monitorsApi.list });
  const distribution = useQuery({
    queryKey: ["stats", "products-by-site"],
    queryFn: statsApi.productsBySite,
  });

  return (
    <>
      <PageHeader
        title="Monitors"
        description="Les plugins de sites chargés — chacun totalement indépendant des autres."
      />

      {monitors.isLoading && <Spinner />}
      {monitors.data && monitors.data.length === 0 && (
        <EmptyState icon={<Radar size={24} />} title="Aucun plugin chargé" />
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="grid gap-3 lg:col-span-2 sm:grid-cols-2">
          {monitors.data?.map((monitor) => (
            <Card key={monitor.site} title={
              <span className="flex items-center gap-2">
                {monitor.display_name}
                {monitor.version && (
                  <span className="text-[10px] font-normal text-faint">v{monitor.version}</span>
                )}
              </span>
            } actions={
              monitor.watched_count > 0
                ? <Badge tone="success" dot pulse>Actif</Badge>
                : <Badge tone="neutral" dot>En veille</Badge>
            }>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
                <dt className="text-faint">Produits</dt>
                <dd className="text-text">{monitor.product_count}
                  <span className="text-faint"> ({monitor.watched_count} surveillé(s))</span>
                </dd>
                <dt className="text-faint">Temps de réponse</dt>
                <dd className="text-text">{formatMs(monitor.avg_response_ms)}</dd>
                <dt className="text-faint">Dernier check</dt>
                <dd className="text-text">{formatTimeAgo(monitor.last_check_at)}</dd>
                <dt className="text-faint">Checks totaux</dt>
                <dd className="text-text">{monitor.total_checks.toLocaleString("fr-FR")}</dd>
                <dt className="text-faint">Dernière erreur</dt>
                <dd className={monitor.last_error ? "text-danger" : "text-text"}>
                  {monitor.last_error
                    ? `${monitor.last_error} (${formatTimeAgo(monitor.last_error_at)})`
                    : "Aucune"}
                </dd>
              </dl>
              {monitor.base_url && (
                <a href={monitor.base_url} target="_blank" rel="noreferrer"
                   className="mt-3 flex items-center gap-1 text-xs text-accent-hover hover:underline">
                  <ExternalLink size={12} />
                  {monitor.base_url}
                </a>
              )}
            </Card>
          ))}
        </div>

        <ChartCard title="Répartition des produits" height={260}
                   isEmpty={(distribution.data?.length ?? 0) === 0}>
          <PieChart>
            <Tooltip
              contentStyle={{
                background: "#17171a", border: "1px solid #26262b",
                borderRadius: 8, fontSize: 12,
              }}
            />
            <Pie data={distribution.data ?? []} dataKey="count" nameKey="site"
                 innerRadius={55} outerRadius={85} paddingAngle={3} strokeWidth={0}
                 label={({ name }) => name}>
              {(distribution.data ?? []).map((entry, index) => (
                <Cell key={entry.site} fill={PIE_COLORS[index % PIE_COLORS.length]} />
              ))}
            </Pie>
          </PieChart>
        </ChartCard>
      </div>
    </>
  );
}
