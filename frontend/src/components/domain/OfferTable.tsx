/** Tableau des offres d'un produit : quel marchand est disponible, tout de suite. */

import { ExternalLink } from "lucide-react";
import type { Offer } from "@/api/types";
import { Badge } from "@/components/ui";
import { AVAILABILITY_META, formatTimeAgo } from "@/lib/format";
import type { Tone } from "@/lib/format";

const OFFER_STATUS_META: Record<Offer["status"], { label: string; tone: Tone }> = {
  active: { label: "Suivie", tone: "success" },
  inactive: { label: "Retirée de la vente", tone: "warning" },
  not_found: { label: "Introuvable", tone: "danger" },
  removed: { label: "Supprimée du site", tone: "danger" },
  archived: { label: "Archivée", tone: "neutral" },
};

/** Le marchand le plus intéressant en premier : stock, puis précommande. */
const AVAILABILITY_RANK: Record<string, number> = {
  in_stock: 0,
  preorder: 1,
  unknown: 2,
  not_listed: 3,
  unavailable: 4,
};

export function OfferTable({
  offers,
  bestSite,
}: {
  offers: Offer[];
  bestSite?: string | null;
}) {
  if (offers.length === 0) {
    return (
      <p className="px-1 py-3 text-xs text-faint">
        Aucune offre référencée pour ce produit.
      </p>
    );
  }

  const sorted = [...offers].sort(
    (a, b) =>
      (AVAILABILITY_RANK[a.availability ?? "unknown"] ?? 9) -
      (AVAILABILITY_RANK[b.availability ?? "unknown"] ?? 9)
  );

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left">
            {["Marchand", "Disponibilité", "Prix", "État", "Vérifié", ""].map(
              (header) => (
                <th
                  key={header}
                  className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-faint"
                >
                  {header}
                </th>
              )
            )}
          </tr>
        </thead>
        <tbody>
          {sorted.map((offer) => {
            const availability = offer.availability
              ? AVAILABILITY_META[offer.availability] ?? AVAILABILITY_META.unknown
              : null;
            const status = OFFER_STATUS_META[offer.status];
            const isBest = bestSite === offer.site;
            return (
              <tr
                key={offer.uuid}
                className={`border-b border-border/60 transition-colors last:border-0 hover:bg-surface-2/60 ${
                  isBest ? "bg-success/5" : ""
                }`}
              >
                <td className="px-3 py-2.5">
                  <span className="font-medium capitalize text-text">
                    {offer.site}
                  </span>
                  {isBest && (
                    <span className="ml-2 text-[10px] font-medium text-success">
                      meilleure offre
                    </span>
                  )}
                </td>
                <td className="px-3 py-2.5">
                  {availability ? (
                    <Badge
                      tone={availability.tone}
                      dot
                      pulse={
                        offer.availability === "in_stock" ||
                        offer.availability === "preorder"
                      }
                    >
                      {availability.label}
                    </Badge>
                  ) : (
                    <Badge tone="neutral">Aucune fiche</Badge>
                  )}
                </td>
                <td className="px-3 py-2.5 font-medium text-text">
                  {offer.price ?? "—"}
                </td>
                <td className="px-3 py-2.5">
                  <Badge tone={status.tone}>{status.label}</Badge>
                </td>
                <td className="px-3 py-2.5 text-muted">
                  {formatTimeAgo(offer.last_checked_at)}
                </td>
                <td className="px-3 py-2.5 text-right">
                  <a
                    href={offer.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-muted transition-colors hover:text-accent-hover"
                    title="Ouvrir la fiche marchande"
                  >
                    <ExternalLink size={14} />
                  </a>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
