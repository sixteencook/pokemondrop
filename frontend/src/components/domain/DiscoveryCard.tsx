/** Carte d'une fiche découverte : visuel, métadonnées et décisions.
 *  Composant de présentation — toutes les actions remontent en props. */

import { Ban, Check, ExternalLink, ImageOff, X } from "lucide-react";
import type { Discovery } from "@/api/types";
import { Badge, Button, Tag } from "@/components/ui";
import { formatTimeAgo } from "@/lib/format";
import type { Tone } from "@/lib/format";

const STATUS_META: Record<Discovery["status"], { label: string; tone: Tone }> = {
  pending: { label: "En attente", tone: "warning" },
  imported: { label: "Surveillé", tone: "success" },
  ignored: { label: "Ignoré", tone: "neutral" },
  blocked: { label: "Toujours ignoré", tone: "danger" },
  gone: { label: "Disparu du site", tone: "neutral" },
};

interface DiscoveryCardProps {
  discovery: Discovery;
  busy?: boolean;
  onApprove: (discovery: Discovery) => void;
  onIgnore: (discovery: Discovery) => void;
  onBlock: (discovery: Discovery) => void;
}

export function DiscoveryCard({
  discovery,
  busy = false,
  onApprove,
  onIgnore,
  onBlock,
}: DiscoveryCardProps) {
  const status = STATUS_META[discovery.status];
  const decided = discovery.status !== "pending";

  return (
    <article className="flex gap-3 rounded-lg border border-border bg-surface p-3 transition-colors hover:border-border-strong animate-fade-up">
      <div className="size-20 shrink-0 overflow-hidden rounded-md border border-border bg-surface-2">
        {discovery.image_url ? (
          <img
            src={discovery.image_url}
            alt={discovery.title}
            loading="lazy"
            className="size-full object-cover"
            onError={(event) => {
              (event.currentTarget as HTMLImageElement).style.display = "none";
            }}
          />
        ) : (
          <span className="flex size-full items-center justify-center text-faint">
            <ImageOff size={18} />
          </span>
        )}
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold text-text">
              {discovery.title}
            </h3>
            <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
              <span className="capitalize">{discovery.site}</span>
              <span className="text-faint">·</span>
              <span>détecté {formatTimeAgo(discovery.first_seen_at)}</span>
              {discovery.source && (
                <>
                  <span className="text-faint">·</span>
                  <Tag>{discovery.source.startsWith("http") ? "listing" : discovery.source}</Tag>
                </>
              )}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {discovery.price && <Badge tone="info">{discovery.price}</Badge>}
            <Badge tone={status.tone} dot>{status.label}</Badge>
          </div>
        </div>

        <a
          href={discovery.url}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1 truncate text-xs text-accent-hover hover:underline"
        >
          <ExternalLink size={12} className="shrink-0" />
          <span className="truncate">{discovery.url}</span>
        </a>

        {discovery.decision_reason && (
          <p className="truncate text-[11px] text-faint">
            {discovery.decision_reason}
          </p>
        )}

        <div className="mt-auto flex flex-wrap gap-1.5">
          <Button
            size="sm"
            variant="primary"
            icon={<Check size={13} />}
            onClick={() => onApprove(discovery)}
            disabled={busy || discovery.status === "imported"}
          >
            Ajouter à la surveillance
          </Button>
          <Button
            size="sm"
            variant="ghost"
            icon={<X size={13} />}
            onClick={() => onIgnore(discovery)}
            disabled={busy || (decided && discovery.status === "ignored")}
          >
            Ignorer
          </Button>
          <Button
            size="sm"
            variant="ghost"
            icon={<Ban size={13} />}
            onClick={() => onBlock(discovery)}
            disabled={busy || discovery.status === "blocked"}
          >
            Toujours ignorer
          </Button>
        </div>
      </div>
    </article>
  );
}
