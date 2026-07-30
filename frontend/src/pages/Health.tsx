/** Santé système : ressources, scheduler, environnement + temps de réponse. */

import { useQuery } from "@tanstack/react-query";
import {
  Cpu,
  Database,
  HardDrive,
  HeartPulse,
  ListTodo,
  Radio,
  Send,
  TimerReset,
} from "lucide-react";
import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import { healthApi, statsApi } from "@/api/endpoints";
import { Badge, ChartCard, PageHeader, Spinner, StatCard } from "@/components/ui";
import { CHART_COLORS } from "@/components/ui/ChartCard";
import { formatDuration } from "@/lib/format";

export default function HealthPage() {
  const health = useQuery({
    queryKey: ["health", "system"],
    queryFn: healthApi.system,
    refetchInterval: 15_000,
  });
  const responseTimes = useQuery({
    queryKey: ["stats", "response-times"],
    queryFn: () => statsApi.checksPerHour(48),
  });

  const data = health.data;

  return (
    <>
      <PageHeader
        title="Santé"
        description="État du serveur, du moteur et de l'environnement."
        actions={
          data && (
            <Badge tone={data.scheduler_running ? "success" : "danger"} dot
                   pulse={data.scheduler_running}>
              {data.scheduler_running ? "Scheduler en marche" : "Scheduler arrêté"}
            </Badge>
          )
        }
      />

      {!data && <Spinner />}
      {data && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <StatCard label="CPU" tone="accent" icon={<Cpu size={16} />}
                    value={data.cpu_percent !== null ? `${data.cpu_percent.toFixed(1)} %` : "—"} />
          <StatCard label="Mémoire" tone="info" icon={<HardDrive size={16} />}
                    value={data.memory_mb !== null ? `${data.memory_mb} Mo` : "—"} />
          <StatCard label="Uptime" tone="neutral" icon={<HeartPulse size={16} />}
                    value={formatDuration(data.uptime_seconds)} />
          <StatCard label="Version" tone="neutral" icon={<Radio size={16} />}
                    value={`v${data.version}`}
                    hint={`Python ${data.python_version}`} />
          <StatCard label="Environnement" tone="accent" icon={<Database size={16} />}
                    value={data.railway_environment ?? "local"}
                    hint={`Base : ${data.database}`} />
          <StatCard label="Watchers actifs" tone="success" icon={<TimerReset size={16} />}
                    value={data.watchers_active} />
          <StatCard label="Telegram" tone={data.telegram_configured ? "success" : "warning"}
                    icon={<Send size={16} />}
                    value={data.telegram_configured ? "Configuré" : "Non configuré"} />
          <StatCard label="Tâches asyncio" tone="neutral" icon={<ListTodo size={16} />}
                    value={data.asyncio_tasks} />
        </div>
      )}

      <div className="mt-5">
        <ChartCard title="Évolution du temps de réponse (48 h)"
                   isEmpty={(responseTimes.data?.length ?? 0) === 0}>
          <LineChart data={responseTimes.data ?? []}>
            <CartesianGrid stroke={CHART_COLORS.grid} vertical={false} />
            <XAxis dataKey="hour" tickFormatter={(hour: string) => hour.slice(11)}
                   stroke={CHART_COLORS.text} fontSize={11} tickLine={false} />
            <YAxis stroke={CHART_COLORS.text} fontSize={11} tickLine={false}
                   width={40} unit=" ms" />
            <Tooltip
              contentStyle={{
                background: "#17171a", border: "1px solid #26262b",
                borderRadius: 8, fontSize: 12,
              }}
            />
            <Line dataKey="avg_response_ms" name="Temps de réponse"
                  stroke={CHART_COLORS.accent} strokeWidth={2} dot={false}
                  connectNulls />
          </LineChart>
        </ChartCard>
      </div>
    </>
  );
}
