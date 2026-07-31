/** Page Découverte : fiches repérées automatiquement et leur validation. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Compass, RefreshCw, Sparkles } from "lucide-react";
import { discoveriesApi } from "@/api/endpoints";
import type { Discovery } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  PageHeader,
  Pagination,
  Select,
  Spinner,
  StatCard,
  useToast,
} from "@/components/ui";
import { DiscoveryCard } from "@/components/domain/DiscoveryCard";
import { formatTimeAgo } from "@/lib/format";
import { ApiError } from "@/api/client";
import { useWsEvent } from "@/ws/WsProvider";

const STATUSES: { value: string; label: string }[] = [
  { value: "pending", label: "En attente" },
  { value: "imported", label: "Surveillés" },
  { value: "ignored", label: "Ignorés" },
  { value: "blocked", label: "Toujours ignorés" },
  { value: "gone", label: "Disparus" },
  { value: "", label: "Tous les statuts" },
];

const MODE_LABELS: Record<string, string> = {
  auto: "Import automatique",
  review: "Validation manuelle",
  rules: "Automatique selon les règles",
};

export default function DiscoveryPage() {
  const queryClient = useQueryClient();
  const { push } = useToast();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<string>("pending");
  const [search, setSearch] = useState("");

  const state = useQuery({
    queryKey: ["discoveries", "state"],
    queryFn: discoveriesApi.state,
  });
  const discoveries = useQuery({
    queryKey: ["discoveries", "list", page, status, search],
    queryFn: () =>
      discoveriesApi.list({
        page,
        page_size: 20,
        status: status || undefined,
        search: search || undefined,
      }),
  });

  // Une fiche vient d'être trouvée : la liste se met à jour toute seule.
  useWsEvent("discovery", () => {
    void queryClient.invalidateQueries({ queryKey: ["discoveries"] });
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["discoveries"] });
    void queryClient.invalidateQueries({ queryKey: ["products"] });
    void queryClient.invalidateQueries({ queryKey: ["stats"] });
  };

  /** Options communes aux trois décisions — les hooks restent au niveau
   *  du composant, appelés inconditionnellement (règles des hooks). */
  const decisionOptions = (
    action: (fingerprint: string) => Promise<Discovery>,
    title: string,
  ) => ({
    mutationFn: (discovery: Discovery) => action(discovery.fingerprint),
    onSuccess: (_result: Discovery, discovery: Discovery) => {
      push({ tone: "success" as const, title, description: discovery.title });
      invalidate();
    },
    onError: (error: unknown) =>
      push({
        tone: "danger" as const,
        title: "Action impossible",
        description: error instanceof ApiError ? error.message : undefined,
      }),
  });

  const approve = useMutation(
    decisionOptions(discoveriesApi.approve, "Ajouté à la surveillance")
  );
  const ignore = useMutation(
    decisionOptions(discoveriesApi.ignore, "Fiche ignorée")
  );
  const block = useMutation(
    decisionOptions(discoveriesApi.block, "Fiche définitivement ignorée")
  );

  const scan = useMutation({
    mutationFn: discoveriesApi.scan,
    onSuccess: (report) => {
      push({ tone: "info", title: "Balayage terminé", description: report.summary });
      invalidate();
    },
    onError: (error) =>
      push({
        tone: "warning",
        title: "Balayage impossible",
        description: error instanceof ApiError ? error.message : undefined,
      }),
  });

  const counts = state.data?.counts ?? {};
  const busy = approve.isPending || ignore.isPending || block.isPending;

  return (
    <>
      <PageHeader
        title="Découverte"
        description="Fiches produit repérées automatiquement sur les sites surveillés."
        actions={
          <>
            {state.data && (
              <Badge tone={state.data.enabled ? "success" : "neutral"} dot
                     pulse={state.data.enabled}>
                {state.data.enabled
                  ? MODE_LABELS[state.data.mode] ?? state.data.mode
                  : "Découverte désactivée"}
              </Badge>
            )}
            <Button
              variant="secondary"
              icon={<RefreshCw size={14} />}
              loading={scan.isPending}
              onClick={() => scan.mutate()}
              disabled={!state.data?.enabled}
              title={
                state.data?.enabled
                  ? "Explorer les sites maintenant"
                  : "Activez la découverte dans config/discovery.yaml"
              }
            >
              Balayer maintenant
            </Button>
          </>
        }
      />

      {state.data && !state.data.enabled && (
        <div className="mb-4 rounded-lg border border-warning/25 bg-warning/10 px-4 py-3 text-xs text-warning">
          La découverte est désactivée. Passez <code>enabled: true</code> dans{" "}
          <code>config/discovery.yaml</code> pour explorer automatiquement les
          sites ; les fiches déjà trouvées restent consultables ici.
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="En attente" tone="warning" icon={<Sparkles size={16} />}
                  value={counts.pending ?? 0} hint="à valider" />
        <StatCard label="Sous surveillance" tone="success" icon={<Compass size={16} />}
                  value={counts.imported ?? 0} />
        <StatCard label="Écartées" tone="neutral"
                  value={(counts.ignored ?? 0) + (counts.blocked ?? 0)} />
        <StatCard label="Dernière trouvaille" tone="accent"
                  value={formatTimeAgo(state.data?.last_discovery_at)}
                  hint={state.data?.sites.join(", ") || "aucun site"} />
      </div>

      {state.data?.last_scan_summary && (
        <p className="mt-3 text-xs text-faint">
          Dernier balayage : {state.data.last_scan_summary}
        </p>
      )}

      <Card padded={false} className="mt-4">
        <div className="flex flex-wrap gap-2 border-b border-border p-3">
          <Select className="h-8 w-44 text-xs" value={status}
                  onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
            {STATUSES.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </Select>
          <Input className="h-8 w-56 text-xs" placeholder="Rechercher un titre…"
                 value={search}
                 onChange={(event) => { setSearch(event.target.value); setPage(1); }} />
        </div>

        <div className="p-3">
          {discoveries.isLoading && <Spinner />}
          {discoveries.data && discoveries.data.items.length === 0 && (
            <EmptyState
              icon={<Compass size={24} />}
              title="Aucune fiche pour ce filtre"
              description={
                status === "pending"
                  ? "Les nouvelles fiches trouvées apparaîtront ici, en temps réel."
                  : "Changez de filtre pour voir d'autres fiches."
              }
            />
          )}
          <div className="grid gap-3">
            {discoveries.data?.items.map((discovery) => (
              <DiscoveryCard
                key={discovery.fingerprint}
                discovery={discovery}
                busy={busy}
                onApprove={(item) => approve.mutate(item)}
                onIgnore={(item) => ignore.mutate(item)}
                onBlock={(item) => block.mutate(item)}
              />
            ))}
          </div>
          {discoveries.data && (
            <Pagination page={discoveries.data.page} pages={discoveries.data.pages}
                        total={discoveries.data.total} onChange={setPage} />
          )}
        </div>
      </Card>
    </>
  );
}
