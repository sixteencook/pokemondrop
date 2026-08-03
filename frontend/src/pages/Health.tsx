/** Santé / Diagnostics — l'outil principal de maintenance du projet.
 *
 *  Objectif de lecture : comprendre en moins de 30 secondes si le moteur
 *  fonctionne, quel plugin pose problème et si une régression est apparue.
 *  L'ordre des blocs suit cette priorité : anomalies d'abord, détail ensuite.
 */

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Boxes,
  Camera,
  Cpu,
  Database,
  Gauge,
  HardDrive,
  HeartPulse,
  Layers,
  PackageSearch,
  Radio,
  Send,
  ShieldAlert,
  Siren,
  Sparkles,
  TimerReset,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { healthApi, statsApi } from "@/api/endpoints";
import type { Anomaly, EngineEvent, PluginHealth } from "@/api/types";
import {
  Badge,
  Card,
  ChartCard,
  EmptyState,
  PageHeader,
  Spinner,
  StatCard,
} from "@/components/ui";
import { CHART_COLORS } from "@/components/ui/ChartCard";
import type { Tone } from "@/lib/format";
import { formatDuration, formatMs, formatTimeAgo } from "@/lib/format";

const TOOLTIP_STYLE = {
  background: "#17171a",
  border: "1px solid #26262b",
  borderRadius: 8,
  fontSize: 12,
};

const STATUS_META: Record<PluginHealth["status"], { label: string; tone: Tone }> = {
  healthy: { label: "Sain", tone: "success" },
  degraded: { label: "Dégradé", tone: "warning" },
  unhealthy: { label: "En difficulté", tone: "danger" },
  observation: { label: "En observation", tone: "neutral" },
};

const SEVERITY_TONE: Record<string, Tone> = {
  info: "neutral",
  warning: "warning",
  error: "danger",
};

/** Un compteur ne s'affiche en couleur que s'il vaut quelque chose. */
function Counter({ label, value, tone = "neutral" }: {
  label: string;
  value: number | string | null;
  tone?: Tone;
}) {
  const isZero = value === 0 || value === null;
  const toneClass = isZero
    ? "text-faint"
    : tone === "danger"
      ? "text-danger"
      : tone === "warning"
        ? "text-warning"
        : "text-text";
  return (
    <div className="flex items-baseline justify-between gap-2 py-1">
      <span className="text-[11px] text-muted">{label}</span>
      <span className={`text-xs font-medium tabular-nums ${toneClass}`}>
        {value ?? "—"}
      </span>
    </div>
  );
}

function ScoreRing({ score, status }: { score: number; status: PluginHealth["status"] }) {
  const meta = STATUS_META[status];
  const color =
    status === "healthy" ? CHART_COLORS.success
      : status === "degraded" ? CHART_COLORS.warning
        : status === "unhealthy" ? CHART_COLORS.danger
          : CHART_COLORS.text;
  return (
    <div className="flex items-center gap-3">
      <div
        className="grid size-14 shrink-0 place-items-center rounded-full"
        style={{ background: `conic-gradient(${color} ${score * 3.6}deg, #26262b 0deg)` }}
      >
        <div className="grid size-11 place-items-center rounded-full bg-surface">
          <span className="text-sm font-semibold tabular-nums">{score}</span>
        </div>
      </div>
      <Badge tone={meta.tone} dot>{meta.label}</Badge>
    </div>
  );
}

function PluginCard({ plugin }: { plugin: PluginHealth }) {
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          {plugin.display_name}
          {plugin.version && (
            <span className="text-[11px] text-faint">v{plugin.version}</span>
          )}
        </span>
      }
      actions={<ScoreRing score={plugin.score} status={plugin.status} />}
    >
      {plugin.main_issue && plugin.status !== "healthy" && (
        <p className="mb-3 rounded-md bg-warning/10 px-2 py-1.5 text-[11px] text-warning">
          Principal poste de pénalité : {plugin.main_issue}
        </p>
      )}

      <div className="grid grid-cols-2 gap-x-5">
        <div>
          <Counter label="Produits surveillés" value={plugin.products_watched} />
          <Counter label="Vérifications (24 h)" value={plugin.checks} />
          <Counter
            label="Succès"
            value={plugin.success_rate !== null ? `${plugin.success_rate} %` : null}
            tone={plugin.success_rate !== null && plugin.success_rate < 95
              ? "warning" : "neutral"}
          />
          <Counter label="Temps moyen" value={formatMs(plugin.avg_response_ms)} />
          <Counter
            label="Confiance moyenne"
            value={plugin.avg_confidence !== null
              ? `${Math.round(plugin.avg_confidence)} %` : null}
          />
          <Counter label="Dernière vérification"
                   value={formatTimeAgo(plugin.last_check_at)} />
        </div>
        <div>
          <Counter label="Erreurs réseau" value={plugin.network_errors} tone="danger" />
          <Counter label="Timeouts" value={plugin.timeouts} tone="danger" />
          <Counter label="403" value={plugin.http_403} tone="danger" />
          <Counter label="429" value={plugin.http_429} tone="danger" />
          <Counter label="5xx" value={plugin.http_5xx} tone="danger" />
          <Counter label="Fiches introuvables" value={plugin.pages_missing}
                   tone="warning" />
        </div>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-x-5 border-t border-border pt-2">
        <div>
          <Counter label="Navigateur utilisé" value={plugin.browser_checks} />
          <Counter label="Bascules navigateur" value={plugin.browser_fallbacks}
                   tone="warning" />
          <Counter label="Interceptions / captchas" value={plugin.captchas}
                   tone="danger" />
        </div>
        <div>
          <Counter label="États indéterminés" value={plugin.unknown_states}
                   tone="warning" />
          <Counter label="Confiance insuffisante" value={plugin.low_confidence}
                   tone="warning" />
          <Counter label="Contexte de livraison" value={plugin.locale_mismatch}
                   tone="warning" />
        </div>
      </div>

      {plugin.last_error && (
        <p className="mt-3 truncate rounded-md bg-surface-3 px-2 py-1.5 text-[11px] text-muted"
           title={plugin.last_error}>
          Dernière erreur ({formatTimeAgo(plugin.last_error_at)}) : {plugin.last_error}
        </p>
      )}
    </Card>
  );
}

function AnomalyRow({ anomaly }: { anomaly: Anomaly }) {
  return (
    <li className="flex gap-3 border-b border-border px-4 py-3 last:border-0">
      <AlertTriangle
        size={16}
        className={`mt-0.5 shrink-0 ${
          anomaly.severity === "error" ? "text-danger" : "text-warning"
        }`}
      />
      <div className="min-w-0">
        <p className="text-xs font-medium text-text">{anomaly.title}</p>
        <p className="mt-0.5 text-[11px] leading-relaxed text-muted">{anomaly.detail}</p>
      </div>
    </li>
  );
}

function EventRow({ event }: { event: EngineEvent }) {
  return (
    <li className="flex items-center gap-3 border-b border-border px-4 py-2 last:border-0">
      <Badge tone={SEVERITY_TONE[event.severity] ?? "neutral"}>{event.source}</Badge>
      <span className="shrink-0 text-xs text-text">{event.label}</span>
      {event.detail && (
        <span className="truncate text-[11px] text-faint">{event.detail}</span>
      )}
      <span className="ml-auto shrink-0 text-[11px] text-faint">
        {formatTimeAgo(event.created_at)}
      </span>
    </li>
  );
}

export default function HealthPage() {
  const diagnostics = useQuery({
    queryKey: ["diagnostics"],
    queryFn: healthApi.diagnostics,
    refetchInterval: 30_000,
  });
  const system = useQuery({
    queryKey: ["health", "system"],
    queryFn: healthApi.system,
    refetchInterval: 30_000,
  });
  const responseTimes = useQuery({
    queryKey: ["stats", "response-times"],
    queryFn: () => statsApi.checksPerHour(48),
  });

  const data = diagnostics.data;
  const overview = data?.overview;
  const sys = system.data;

  return (
    <>
      <PageHeader
        title="Santé"
        description="État du moteur, santé des plugins et anomalies détectées automatiquement."
        actions={
          overview && (
            <Badge tone={overview.engine_running ? "success" : "danger"} dot
                   pulse={overview.engine_running}>
              {overview.engine_running ? "Moteur en marche" : "Moteur arrêté"}
            </Badge>
          )
        }
      />

      {!data && <Spinner />}

      {data && overview && (
        <>
          {/* --- Score système : la première question qu'on se pose --- */}
          <Card className="mb-5">
            <div className="flex flex-wrap items-center gap-6">
              <div className="flex items-center gap-3">
                <ScoreRing score={data.system.score}
                           status={data.system.status as PluginHealth["status"]} />
                <div>
                  <p className="text-xs font-medium text-text">Santé globale</p>
                  <p className="text-[11px] text-faint">
                    Moyenne pondérée de tous les composants
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap gap-x-6 gap-y-1">
                {data.system.components.map((component) => (
                  <div key={component.key} className="min-w-24">
                    <p className="text-[11px] text-muted">{component.name}</p>
                    <p className={`text-sm font-semibold tabular-nums ${
                      component.score >= 90 ? "text-success"
                        : component.score >= 70 ? "text-warning" : "text-danger"
                    }`}>
                      {component.score} %
                    </p>
                  </div>
                ))}
              </div>
            </div>
          </Card>

          {/* --- Anomalies : c'est ce qu'on vient chercher --- */}
          <Card
            title={
              <span className="flex items-center gap-2">
                <Siren size={15} />
                Problèmes détectés
                {data.anomalies.length > 0 && (
                  <Badge tone="warning">{data.anomalies.length}</Badge>
                )}
              </span>
            }
            padded={false}
            className="mb-5"
          >
            {data.anomalies.length === 0 ? (
              <EmptyState
                icon={<Sparkles size={24} />}
                title="Aucune anomalie détectée"
                description="Les plugins se comportent comme la semaine précédente."
              />
            ) : (
              <ul>
                {data.anomalies.map((anomaly, index) => (
                  <AnomalyRow key={`${anomaly.source}-${index}`} anomaly={anomaly} />
                ))}
              </ul>
            )}
          </Card>

          {/* --- Vue globale --- */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            <StatCard label="Plugins actifs" tone="accent" icon={<Layers size={16} />}
                      value={overview.plugins_active} />
            <StatCard label="Produits surveillés" tone="info"
                      icon={<PackageSearch size={16} />}
                      value={overview.products_watched}
                      hint={`${overview.products_total} en base`} />
            <StatCard label="Offres" tone="neutral" icon={<Boxes size={16} />}
                      value={overview.offers_total}
                      hint={`${overview.canonical_products} produits canoniques`} />
            <StatCard label="Vérifications (24 h)" tone="neutral"
                      icon={<Activity size={16} />} value={overview.checks_today} />
            <StatCard label="Alertes (24 h)" tone="success" icon={<Send size={16} />}
                      value={overview.alerts_today} />
            <StatCard label="Erreurs (24 h)"
                      tone={overview.errors_today > 0 ? "danger" : "neutral"}
                      icon={<ShieldAlert size={16} />} value={overview.errors_today} />
            <StatCard label="Découvertes (24 h)" tone="accent"
                      icon={<Sparkles size={16} />} value={overview.discoveries_today}
                      hint={`${overview.discoveries_pending} en attente`} />
            <StatCard label="Temps d'analyse moyen" tone="info"
                      icon={<TimerReset size={16} />}
                      value={formatMs(overview.avg_response_ms)}
                      hint={Object.entries(overview.avg_response_by_plugin)
                        .map(([site, ms]) => `${site} ${formatMs(ms)}`)
                        .join(" · ") || undefined} />
          </div>

          {/* --- Santé des plugins --- */}
          <h2 className="mb-3 mt-6 text-sm font-medium text-text">Santé des plugins</h2>
          <div className="grid gap-3 lg:grid-cols-2">
            {data.plugins.map((plugin) => (
              <PluginCard key={plugin.site} plugin={plugin} />
            ))}
          </div>

          {/* --- Découverte et Product Intelligence --- */}
          <div className="mt-6 grid gap-3 lg:grid-cols-2">
            <Card title="Découverte">
              <div className="grid grid-cols-2 gap-x-5">
                <div>
                  <Counter label="Aujourd'hui" value={data.discovery.found_today} />
                  <Counter label="Cette semaine"
                           value={data.discovery.found_this_week} />
                  <Counter label="Importées automatiquement"
                           value={data.discovery.imported} />
                </div>
                <div>
                  <Counter label="En attente de validation"
                           value={data.discovery.pending} tone="warning" />
                  <Counter label="Ignorées" value={data.discovery.ignored} />
                  <Counter label="Bloquées" value={data.discovery.blocked} />
                </div>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-x-5 border-t border-border pt-2">
                <div>
                  <Counter label="Recherches inter-sites"
                           value={data.discovery.searches_total} />
                  <Counter label="Recherches abouties"
                           value={data.discovery.searches_found} />
                </div>
                <div>
                  <Counter label="Sans résultat"
                           value={data.discovery.searches_empty} />
                  <Counter label="Relance en attente"
                           value={data.discovery.searches_retrying}
                           tone="warning" />
                </div>
              </div>
              <p className="mt-2 border-t border-border pt-2 text-[11px] text-faint">
                Dernière découverte : {formatTimeAgo(data.discovery.last_discovery_at)}
              </p>
            </Card>

            <Card title="Product Intelligence">
              <div className="grid grid-cols-2 gap-x-5">
                <div>
                  <Counter label="Produits canoniques"
                           value={data.intelligence.canonical_products} />
                  <Counter label="Offres" value={data.intelligence.offers} />
                  <Counter label="Fusions automatiques"
                           value={data.intelligence.merged_automatically} />
                  <Counter label="À valider"
                           value={data.intelligence.pending_validation}
                           tone="warning" />
                </div>
                <div>
                  {Object.entries(data.intelligence.identifiers).map(([key, value]) => (
                    <Counter key={key} label={key.toUpperCase()} value={value} />
                  ))}
                </div>
              </div>
              <p className="mt-2 border-t border-border pt-2 text-[11px] text-faint">
                Confiance moyenne des rapprochements proposés :{" "}
                {data.intelligence.avg_confidence !== null
                  ? `${data.intelligence.avg_confidence} %`
                  : "—"}
              </p>
            </Card>
          </div>

          {/* --- Graphiques --- */}
          <div className="mt-6 grid gap-3 lg:grid-cols-2">
            <ChartCard title="Temps d'analyse moyen (48 h)"
                       isEmpty={(responseTimes.data?.length ?? 0) === 0}>
              <LineChart data={responseTimes.data ?? []}>
                <CartesianGrid stroke={CHART_COLORS.grid} vertical={false} />
                <XAxis dataKey="hour" tickFormatter={(hour: string) => hour.slice(11)}
                       stroke={CHART_COLORS.text} fontSize={11} tickLine={false} />
                <YAxis stroke={CHART_COLORS.text} fontSize={11} tickLine={false}
                       width={44} unit=" ms" />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Line dataKey="avg_response_ms" name="Temps moyen"
                      stroke={CHART_COLORS.accent} strokeWidth={2} dot={false}
                      connectNulls />
              </LineChart>
            </ChartCard>

            <ChartCard title="Incidents par heure (48 h)"
                       isEmpty={data.charts.incidents_per_hour.length === 0}
                       emptyLabel="Aucun incident sur la période">
              <BarChart data={data.charts.incidents_per_hour}>
                <CartesianGrid stroke={CHART_COLORS.grid} vertical={false} />
                <XAxis dataKey="hour" tickFormatter={(hour: string) => hour.slice(11)}
                       stroke={CHART_COLORS.text} fontSize={11} tickLine={false} />
                <YAxis stroke={CHART_COLORS.text} fontSize={11} tickLine={false}
                       width={30} allowDecimals={false} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Bar dataKey="unknown_state" name="Indéterminés" stackId="i"
                     fill={CHART_COLORS.warning} />
                <Bar dataKey="http_error" name="Erreurs HTTP" stackId="i"
                     fill={CHART_COLORS.danger} />
                <Bar dataKey="browser_fallback" name="Bascules navigateur" stackId="i"
                     fill={CHART_COLORS.info} />
                <Bar dataKey="blocked" name="Interceptions" stackId="i"
                     fill={CHART_COLORS.accent} />
              </BarChart>
            </ChartCard>

            <ChartCard title="Confiance moyenne d'analyse (48 h)"
                       isEmpty={data.charts.confidence_per_hour.length === 0}
                       emptyLabel="Aucune confiance mesurée">
              <LineChart data={data.charts.confidence_per_hour}>
                <CartesianGrid stroke={CHART_COLORS.grid} vertical={false} />
                <XAxis dataKey="hour" tickFormatter={(hour: string) => hour.slice(11)}
                       stroke={CHART_COLORS.text} fontSize={11} tickLine={false} />
                <YAxis stroke={CHART_COLORS.text} fontSize={11} tickLine={false}
                       width={34} domain={[0, 100]} unit=" %" />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Line dataKey="avg_confidence" name="Confiance"
                      stroke={CHART_COLORS.success} strokeWidth={2} dot={false}
                      connectNulls />
              </LineChart>
            </ChartCard>

            <ChartCard title="Alertes par jour (14 j)"
                       isEmpty={data.charts.alerts_per_day.length === 0}>
              <BarChart data={data.charts.alerts_per_day}>
                <CartesianGrid stroke={CHART_COLORS.grid} vertical={false} />
                <XAxis dataKey="day" stroke={CHART_COLORS.text} fontSize={11}
                       tickLine={false} />
                <YAxis stroke={CHART_COLORS.text} fontSize={11} tickLine={false}
                       width={30} allowDecimals={false} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Bar dataKey="total" name="Alertes" fill={CHART_COLORS.success} />
              </BarChart>
            </ChartCard>

            <ChartCard title="Produits découverts par jour (14 j)"
                       isEmpty={data.charts.discoveries_per_day.length === 0}>
              <BarChart data={data.charts.discoveries_per_day}>
                <CartesianGrid stroke={CHART_COLORS.grid} vertical={false} />
                <XAxis dataKey="day" stroke={CHART_COLORS.text} fontSize={11}
                       tickLine={false} />
                <YAxis stroke={CHART_COLORS.text} fontSize={11} tickLine={false}
                       width={30} allowDecimals={false} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Bar dataKey="total" name="Découvertes" fill={CHART_COLORS.accent} />
              </BarChart>
            </ChartCard>
          </div>

          {/* --- Temps par étage du moteur --- */}
          <h2 className="mb-3 mt-6 text-sm font-medium text-text">
            Temps moyen par étage
          </h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <StatCard label="Requête HTTP" tone="success"
                      icon={<Gauge size={16} />}
                      value={formatMs(data.timings.http_ms)} />
            <StatCard label="Rendu navigateur" tone="warning"
                      icon={<Gauge size={16} />}
                      value={formatMs(data.timings.browser_ms)} />
            <StatCard label="Capture d'écran" tone="info"
                      icon={<Camera size={16} />}
                      value={formatMs(data.timings.screenshot_ms)} />
            <StatCard label="Balayage Discovery" tone="accent"
                      icon={<Sparkles size={16} />}
                      value={formatMs(data.timings.discovery_scan_ms)} />
            <StatCard label="Corrélation Intelligence" tone="neutral"
                      icon={<Boxes size={16} />}
                      value={formatMs(data.timings.intelligence_ms)} />
          </div>

          {/* --- Incidents : la vie du moteur, racontée --- */}
          <Card
            title={
              <span className="flex items-center gap-2">
                <ShieldAlert size={15} />
                Incidents
                {data.incidents.length > 0 && (
                  <Badge tone="warning">{data.incidents.length}</Badge>
                )}
              </span>
            }
            padded={false}
            className="mt-6"
          >
            {data.incidents.length === 0 ? (
              <EmptyState
                icon={<Sparkles size={24} />}
                title="Aucun incident"
                description="Le moteur n'a rencontré aucun refus, timeout ni interception."
              />
            ) : (
              <ul className="max-h-96 overflow-y-auto">
                {data.incidents.map((incident, index) => (
                  <li key={`${incident.source}-${index}`}
                      className="border-b border-border px-4 py-3 last:border-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="neutral">{incident.source}</Badge>
                      {incident.steps.map((step, position) => (
                        <span key={position}
                              className="flex items-center gap-2 text-[11px]">
                          {position > 0 && (
                            <ArrowRight size={11} className="text-faint" />
                          )}
                          <span className={
                            step.severity === "error" ? "text-danger"
                              : step.severity === "warning" ? "text-warning"
                                : "text-muted"
                          }>
                            {step.label}
                          </span>
                        </span>
                      ))}
                      <span className={`ml-auto text-[11px] ${
                        incident.outcome === "non résolu"
                          ? "text-danger" : "text-success"
                      }`}>
                        {incident.outcome}
                      </span>
                      <span className="shrink-0 text-[11px] text-faint">
                        {formatTimeAgo(incident.started_at)}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {/* --- Historique technique --- */}
          <Card
            title={`Historique technique (${data.history.length} derniers événements)`}
            padded={false}
            className="mt-6"
          >
            {data.history.length === 0 ? (
              <EmptyState
                icon={<Activity size={24} />}
                title="Aucun événement technique"
                description="Un cycle nominal n'en produit aucun : c'est bon signe."
              />
            ) : (
              <ul className="max-h-96 overflow-y-auto">
                {data.history.map((event) => (
                  <EventRow key={event.id} event={event} />
                ))}
              </ul>
            )}
          </Card>
        </>
      )}

      {/* --- Ressources du serveur --- */}
      {sys && (
        <>
          <h2 className="mb-3 mt-6 text-sm font-medium text-text">Serveur</h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <StatCard label="CPU" tone="accent" icon={<Cpu size={16} />}
                      value={sys.cpu_percent !== null
                        ? `${sys.cpu_percent.toFixed(1)} %` : "—"} />
            <StatCard label="Mémoire" tone="info" icon={<HardDrive size={16} />}
                      value={sys.memory_mb !== null ? `${sys.memory_mb} Mo` : "—"} />
            <StatCard label="Uptime" tone="neutral" icon={<HeartPulse size={16} />}
                      value={formatDuration(sys.uptime_seconds)} />
            <StatCard label="Version" tone="neutral" icon={<Radio size={16} />}
                      value={`v${sys.version}`}
                      hint={`Python ${sys.python_version}`} />
            <StatCard label="Environnement" tone="accent" icon={<Database size={16} />}
                      value={sys.railway_environment ?? "local"}
                      hint={`Base : ${sys.database}`} />
            <StatCard label="Telegram"
                      tone={sys.telegram_configured ? "success" : "warning"}
                      icon={<Send size={16} />}
                      value={sys.telegram_configured ? "Configuré" : "Non configuré"}
                      hint={`${sys.asyncio_tasks} tâches asyncio`} />
          </div>
        </>
      )}
    </>
  );
}
