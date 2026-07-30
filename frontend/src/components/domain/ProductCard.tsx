/** Carte produit du dashboard : état + actions rapides.
 *  Composant de présentation : toutes les actions remontent en props. */

import {
  ExternalLink,
  History,
  Pause,
  Pencil,
  Play,
  RefreshCw,
} from "lucide-react";
import type { Product } from "@/api/types";
import { Badge, Button, Tag } from "@/components/ui";
import { PRIORITY_META, formatTimeAgo } from "@/lib/format";
import { AvailabilityBadge } from "./AvailabilityBadge";

interface ProductCardProps {
  product: Product;
  busy?: boolean;
  onToggle: (product: Product) => void;
  onEdit: (product: Product) => void;
  onCheckNow: (product: Product) => void;
  onHistory: (product: Product) => void;
}

export function ProductCard({
  product,
  busy = false,
  onToggle,
  onEdit,
  onCheckNow,
  onHistory,
}: ProductCardProps) {
  const priority = PRIORITY_META[product.priority];
  return (
    <article className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4 transition-colors hover:border-border-strong animate-fade-up">
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-text">{product.name}</h3>
          <p className="mt-0.5 flex items-center gap-1.5 text-xs text-muted">
            <span className="capitalize">{product.site}</span>
            <span className="text-faint">·</span>
            <span>{product.check_interval} s</span>
            <span className="text-faint">·</span>
            <span>{formatTimeAgo(product.last_checked_at)}</span>
          </p>
        </div>
        <AvailabilityBadge
          availability={product.availability}
          monitorable={product.monitorable}
        />
      </header>

      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={priority.tone}>{priority.label}</Badge>
        {product.price && <Badge tone="info">{product.price}</Badge>}
        {product.tags.slice(0, 4).map((tag) => (
          <Tag key={tag}>{tag}</Tag>
        ))}
      </div>

      {product.url ? (
        <a
          href={product.url}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1 truncate text-xs text-accent-hover hover:underline"
        >
          <ExternalLink size={12} className="shrink-0" />
          <span className="truncate">{product.url}</span>
        </a>
      ) : (
        <p className="text-xs italic text-faint">URL non renseignée (page pas encore publiée)</p>
      )}

      <footer className="mt-auto flex flex-wrap gap-1.5 border-t border-border pt-3">
        <Button
          size="sm"
          variant={product.enabled ? "ghost" : "secondary"}
          icon={product.enabled ? <Pause size={13} /> : <Play size={13} />}
          onClick={() => onToggle(product)}
          disabled={busy}
        >
          {product.enabled ? "Désactiver" : "Activer"}
        </Button>
        <Button size="sm" variant="ghost" icon={<Pencil size={13} />}
                onClick={() => onEdit(product)}>
          Modifier
        </Button>
        <Button
          size="sm"
          variant="ghost"
          icon={<RefreshCw size={13} />}
          onClick={() => onCheckNow(product)}
          disabled={!product.url || busy}
          title={product.url ? "Vérifier maintenant" : "URL requise"}
        >
          Vérifier
        </Button>
        <Button size="sm" variant="ghost" icon={<History size={13} />}
                onClick={() => onHistory(product)}>
          Historique
        </Button>
      </footer>
    </article>
  );
}
