/** L'histoire d'un produit canonique : ce qu'il a vécu, chez qui, et quand.
 *
 *  Trois lectures dans un seul panneau :
 *    - la PROPAGATION : quel marchand a publié sa fiche en premier ;
 *    - les MÉTRIQUES : ce que ce produit a coûté et rapporté en surveillance ;
 *    - la TIMELINE : le récit chronologique, tous marchands confondus.
 */

import { useQuery } from "@tanstack/react-query";
import { History, Radar } from "lucide-react";
import { catalogApi } from "@/api/endpoints";
import type { StoryEntry } from "@/api/types";
import { Badge, EmptyState, Spinner } from "@/components/ui";
import type { Tone } from "@/lib/format";
import { formatDateTime, formatTimeAgo } from "@/lib/format";

const ORIGIN_TONE: Record<StoryEntry["origin"], Tone> = {
  monitoring: "info",
  discovery: "accent",
  intelligence: "neutral",
};

function Metric({ label, value }: { label: string; value: string | number | null }) {
  return (
    <div className="rounded-md bg-surface-3 px-2 py-1.5">
      <p className="text-[10px] uppercase tracking-wide text-faint">{label}</p>
      <p className="text-xs font-medium tabular-nums text-text">{value ?? "—"}</p>
    </div>
  );
}

export function ProductStoryPanel({ productUuid }: { productUuid: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["catalog", "story", productUuid],
    queryFn: () => catalogApi.story(productUuid),
  });

  if (isLoading) return <Spinner />;
  if (!data) return null;

  const { metrics, propagation, timeline } = data;

  return (
    <div className="space-y-4">
      {/* --- Métriques métier --- */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        <Metric label="Marchands" value={metrics.merchants} />
        <Metric label="Premier" value={metrics.first_merchant} />
        <Metric label="Découvert" value={formatTimeAgo(metrics.first_seen_at)} />
        <Metric label="Changements" value={metrics.changes} />
        <Metric label="Notifications" value={metrics.notifications} />
        <Metric label="Captures" value={metrics.screenshots} />
        <Metric label="Prix modifiés" value={metrics.price_changes} />
        <Metric label="Précommandes" value={metrics.preorders} />
        <Metric label="Invitations" value={metrics.invitations} />
        <Metric label="Retours en stock" value={metrics.back_in_stock} />
        <Metric label="Ruptures" value={metrics.out_of_stock} />
        <Metric label="Dernier marchand" value={metrics.last_merchant} />
      </div>

      {/* --- Propagation entre marchands --- */}
      {propagation.length > 0 && (
        <div>
          <h4 className="mb-2 flex items-center gap-1.5 text-xs font-medium text-text">
            <Radar size={13} />
            Propagation — qui publie le plus tôt
          </h4>
          <ol className="flex flex-wrap items-center gap-1.5">
            {propagation.map((step) => (
              <li key={step.site}
                  className="flex items-center gap-1.5 rounded-md border border-border
                             bg-surface px-2 py-1">
                <span className="text-[10px] text-faint">#{step.rank}</span>
                <span className="text-xs text-text">{step.site}</span>
                <span className="text-[10px] text-muted">
                  {step.rank === 1
                    ? formatDateTime(step.first_seen_at).slice(0, 10)
                    : `+${step.delay_hours} h`}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* --- Identité connue --- */}
      {Object.keys(data.identity).length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(data.identity).map(([key, value]) => (
            <Badge key={key} tone="neutral">
              {key.toUpperCase()} {value}
            </Badge>
          ))}
        </div>
      )}

      {/* --- Le récit --- */}
      <div>
        <h4 className="mb-2 flex items-center gap-1.5 text-xs font-medium text-text">
          <History size={13} />
          Histoire du produit
        </h4>
        {timeline.length === 0 ? (
          <EmptyState
            icon={<History size={22} />}
            title="Aucun événement"
            description="L'histoire commencera dès la première découverte."
          />
        ) : (
          <ol className="max-h-72 space-y-1 overflow-y-auto pr-1">
            {timeline.map((entry, index) => (
              <li key={index} className="flex items-center gap-2 text-[11px]">
                <span className="w-24 shrink-0 text-faint">
                  {formatDateTime(entry.at).slice(0, 16)}
                </span>
                <Badge tone={ORIGIN_TONE[entry.origin]}>{entry.site}</Badge>
                <span className="text-text">{entry.label}</span>
                {entry.detail && (
                  <span className="truncate text-faint">{entry.detail}</span>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>

      {/* --- Recherches inter-sites --- */}
      {data.searches.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-medium text-text">
            Recherches inter-sites
          </h4>
          <ul className="space-y-1">
            {data.searches.slice(0, 12).map((attempt, index) => (
              <li key={index} className="flex items-center gap-2 text-[11px]">
                <Badge tone={attempt.status === "found" ? "success" : "neutral"}>
                  {attempt.site}
                </Badge>
                <span className="text-muted">
                  {attempt.key_kind} · {attempt.attempts} tentative(s)
                </span>
                {attempt.next_retry_at && (
                  <span className="text-warning">
                    relance {formatTimeAgo(attempt.next_retry_at)}
                  </span>
                )}
                <span className="ml-auto shrink-0 text-faint">
                  {formatTimeAgo(attempt.last_attempt_at)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
