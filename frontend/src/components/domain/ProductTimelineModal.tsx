/** Détail d'un produit : sa timeline métier et sa santé technique.
 *
 *  Deux onglets, deux questions distinctes :
 *    « Historique » — qu'est-il arrivé au produit ?
 *    « Santé »      — la surveillance de ce produit fonctionne-t-elle ?
 */

import type { ReactNode } from "react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, History } from "lucide-react";
import type { Product } from "@/api/types";
import { healthApi, productsApi } from "@/api/endpoints";
import { Badge, EmptyState, Modal, Spinner, TimelineList } from "@/components/ui";
import type { Tone } from "@/lib/format";
import {
  AVAILABILITY_META,
  EVENT_TYPE_META,
  formatDateTime,
  formatMs,
  formatTimeAgo,
} from "@/lib/format";

interface ProductTimelineModalProps {
  product: Product | null;
  onClose: () => void;
}

type Tab = "timeline" | "health";

const SEVERITY_TONE: Record<string, Tone> = {
  info: "neutral",
  warning: "warning",
  error: "danger",
};

function Line({ label, value, tone = "neutral" }: {
  label: string;
  value: string | number | null;
  tone?: Tone;
}) {
  const isEmpty = value === null || value === 0;
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border py-1.5 last:border-0">
      <span className="text-[11px] text-muted">{label}</span>
      <span
        className={`text-xs font-medium tabular-nums ${
          isEmpty ? "text-faint"
            : tone === "danger" ? "text-danger"
              : tone === "warning" ? "text-warning" : "text-text"
        }`}
      >
        {value ?? "—"}
      </span>
    </div>
  );
}

function HealthTab({ uuid }: { uuid: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["health", "product", uuid],
    queryFn: () => healthApi.product(uuid),
  });

  if (isLoading) return <Spinner />;
  if (!data) return null;

  // La disponibilité vient de la base : elle peut porter une valeur écrite
  // par une version antérieure. On retombe donc sur le code brut.
  const availability = data.last_availability
    ? (AVAILABILITY_META as Record<string, { label: string; tone: Tone }>)[
        data.last_availability
      ]
    : undefined;

  return (
    <div className="space-y-4">
      <div className="grid gap-x-6 sm:grid-cols-2">
        <div>
          <Line label="État actuel"
                value={availability?.label ?? data.last_availability} />
          <Line label="Dernière analyse" value={formatTimeAgo(data.last_check_at)} />
          <Line label="Dernière notification"
                value={formatTimeAgo(data.last_alert_at)} />
          <Line label="Dernier événement métier" value={data.last_alert_type} />
        </div>
        <div>
          <Line label="Vérifications (total)" value={data.checks_total} />
          <Line label="Vérifications (24 h)" value={data.checks_window} />
          <Line label="Temps moyen" value={formatMs(data.avg_response_ms)} />
          <Line
            label="Confiance moyenne"
            value={data.avg_confidence !== null
              ? `${Math.round(data.avg_confidence)} %` : null}
          />
        </div>
      </div>

      <div className="grid gap-x-6 sm:grid-cols-2">
        <div>
          <Line label="Erreurs (24 h)" value={data.errors} tone="danger" />
          <Line label="États indéterminés" value={data.unknown_states}
                tone="warning" />
          <Line label="Passages par le navigateur" value={data.browser_checks} />
        </div>
        <div>
          <Line label="Dernière erreur"
                value={data.last_error ? formatTimeAgo(data.last_error_at) : "aucune"}
                tone={data.last_error ? "danger" : "neutral"} />
          <Line label="Dernière capture"
                value={data.last_screenshot ? "disponible" : null} />
          <Line label="Dernier HTML archivé"
                value={data.last_evidence ? "disponible" : null} />
        </div>
      </div>

      {data.last_error && (
        <p className="rounded-md bg-danger/10 px-2 py-1.5 text-[11px] text-danger">
          {data.last_error}
        </p>
      )}

      <div>
        <h3 className="mb-2 text-xs font-medium text-text">
          Derniers événements techniques
        </h3>
        {data.recent_events.length === 0 ? (
          <p className="text-[11px] text-faint">
            Aucun incident : un cycle nominal n'en produit pas.
          </p>
        ) : (
          <ul className="space-y-1">
            {data.recent_events.map((event, index) => (
              <li key={`${event.kind}-${index}`}
                  className="flex items-center gap-2 text-[11px]">
                <Badge tone={SEVERITY_TONE[event.severity] ?? "neutral"}>
                  {event.label}
                </Badge>
                <span className="truncate text-faint">{event.detail}</span>
                <span className="ml-auto shrink-0 text-faint">
                  {formatTimeAgo(event.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function TimelineTab({ uuid }: { uuid: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["timeline", "product", uuid],
    queryFn: () => productsApi.timeline(uuid, { page_size: 100 }),
  });

  if (isLoading) return <Spinner />;
  if (!data || data.items.length === 0) {
    return (
      <EmptyState
        icon={<History size={24} />}
        title="Aucun événement"
        description="La timeline se remplira dès que la surveillance démarrera."
      />
    );
  }

  return (
    <TimelineList
      items={data.items.map((entry) => ({
        id: entry.id,
        label: entry.label,
        time: formatDateTime(entry.created_at),
        tone: EVENT_TYPE_META[entry.event_type]?.tone ?? "neutral",
        meta:
          entry.old_value || entry.new_value
            ? `${entry.old_value ?? "—"} → ${entry.new_value ?? "—"}` +
              (entry.price ? ` · ${entry.price}` : "")
            : entry.price ?? undefined,
      }))}
    />
  );
}

export function ProductTimelineModal({ product, onClose }: ProductTimelineModalProps) {
  const [tab, setTab] = useState<Tab>("timeline");

  const tabs: { id: Tab; label: string; icon: ReactNode }[] = [
    { id: "timeline", label: "Historique", icon: <History size={13} /> },
    { id: "health", label: "Santé", icon: <Activity size={13} /> },
  ];

  return (
    <Modal
      open={product !== null}
      title={product ? product.name : "Produit"}
      onClose={onClose}
    >
      <div className="mb-4 flex gap-1 border-b border-border">
        {tabs.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() => setTab(entry.id)}
            className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs
              transition-colors ${
                tab === entry.id
                  ? "border-accent text-text"
                  : "border-transparent text-muted hover:text-text"
              }`}
          >
            {entry.icon}
            {entry.label}
          </button>
        ))}
      </div>

      {product && tab === "timeline" && <TimelineTab uuid={product.uuid} />}
      {product && tab === "health" && <HealthTab uuid={product.uuid} />}
    </Modal>
  );
}
