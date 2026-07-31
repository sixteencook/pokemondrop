/** Identité d'un produit et historique de ses recherches inter-sites.
 *
 *  Répond à deux questions : « que sait-on de ce produit ? » et
 *  « pourquoi ce rapprochement a-t-il été fait ? ».
 */

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Clock, HelpCircle, XCircle } from "lucide-react";
import { catalogApi } from "@/api/endpoints";
import type { SearchAttempt } from "@/api/types";
import { Badge, Spinner, Tag } from "@/components/ui";
import { formatDateTime, formatTimeAgo } from "@/lib/format";
import type { Tone } from "@/lib/format";

const FIELD_LABELS: Record<string, string> = {
  ean: "EAN",
  upc: "UPC",
  isbn: "ISBN",
  gtin: "GTIN",
  asin: "ASIN",
  sku: "SKU",
  mpn: "MPN",
  manufacturer_part_number: "Réf. constructeur",
  model_number: "Numéro de modèle",
  brand: "Marque",
  manufacturer: "Fabricant",
  collection: "Collection",
  edition: "Édition",
  release_date: "Date de sortie",
  canonical_name: "Nom canonique",
  primary_image: "Image principale",
};

const STATUS_META: Record<
  SearchAttempt["status"],
  { label: string; tone: Tone; icon: typeof CheckCircle2 }
> = {
  found: { label: "Trouvé", tone: "success", icon: CheckCircle2 },
  not_found: { label: "Aucun résultat", tone: "neutral", icon: XCircle },
  error: { label: "Erreur", tone: "danger", icon: XCircle },
  unsupported: { label: "Clé non exploitable", tone: "neutral", icon: HelpCircle },
  pending: { label: "En attente", tone: "warning", icon: Clock },
};

function confidenceTone(confidence: number): Tone {
  if (confidence >= 90) return "success";
  if (confidence >= 75) return "warning";
  return "neutral";
}

export function ProductIdentityPanel({ productUuid }: { productUuid: string }) {
  const identity = useQuery({
    queryKey: ["catalog", "identity", productUuid],
    queryFn: () => catalogApi.identity(productUuid),
  });
  const attempts = useQuery({
    queryKey: ["catalog", "attempts", productUuid],
    queryFn: () => catalogApi.searchAttempts(productUuid),
  });

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {/* --- Identité --- */}
      <section>
        <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-faint">
          Identité
        </h4>
        {identity.isLoading && <Spinner />}
        {identity.data && (
          <>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
              {identity.data.fields
                .filter((entry) => entry.field !== "primary_image")
                .map((entry) => (
                  <div key={entry.field} className="contents">
                    <dt className="text-faint">
                      {FIELD_LABELS[entry.field] ?? entry.field}
                    </dt>
                    <dd className="flex min-w-0 items-center gap-2">
                      <span className="truncate font-mono text-xs text-text">
                        {entry.value}
                      </span>
                      <Badge tone={confidenceTone(entry.confidence)}>
                        {entry.confidence}
                      </Badge>
                      {entry.source && <Tag>{entry.source}</Tag>}
                    </dd>
                  </div>
                ))}
            </dl>

            {identity.data.aliases.length > 0 && (
              <div className="mt-3">
                <p className="mb-1 text-xs text-faint">Autres titres connus</p>
                <ul className="grid gap-0.5 text-xs text-muted">
                  {identity.data.aliases.map((alias) => (
                    <li key={alias} className="truncate">· {alias}</li>
                  ))}
                </ul>
              </div>
            )}

            {identity.data.search_keys.length > 0 && (
              <div className="mt-3">
                <p className="mb-1 text-xs text-faint">
                  Clés de recherche ({identity.data.search_keys.length})
                </p>
                <div className="flex flex-wrap gap-1">
                  {identity.data.search_keys.map((key) => (
                    <Tag key={key}>{key}</Tag>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </section>

      {/* --- Recherches --- */}
      <section>
        <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-faint">
          Recherches inter-sites
        </h4>
        {attempts.isLoading && <Spinner />}
        {attempts.data && attempts.data.length === 0 && (
          <p className="text-xs text-faint">
            Aucune recherche lancée pour ce produit.
          </p>
        )}
        {attempts.data && attempts.data.length > 0 && (
          <ul className="grid gap-1.5">
            {attempts.data.map((attempt) => {
              const meta = STATUS_META[attempt.status] ?? STATUS_META.pending;
              const Icon = meta.icon;
              return (
                <li
                  key={attempt.id}
                  className="rounded-md border border-border bg-surface-2 px-2.5 py-2 text-xs"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex min-w-0 items-center gap-1.5">
                      <Icon
                        size={13}
                        className={
                          attempt.status === "found"
                            ? "text-success"
                            : attempt.status === "error"
                              ? "text-danger"
                              : "text-faint"
                        }
                      />
                      <span className="font-medium capitalize text-text">
                        {attempt.site}
                      </span>
                      <Tag>{attempt.key_kind}</Tag>
                    </span>
                    <Badge tone={meta.tone}>{meta.label}</Badge>
                  </div>
                  <p className="mt-1 truncate text-muted">
                    {attempt.reason || "—"}
                    {attempt.confidence > 0 && ` · confiance ${attempt.confidence}`}
                  </p>
                  <p className="mt-0.5 text-[11px] text-faint">
                    {attempt.attempts} tentative(s) · dernière{" "}
                    {formatTimeAgo(attempt.last_attempt_at)}
                    {attempt.next_retry_at && (
                      <> · relance le {formatDateTime(attempt.next_retry_at)}</>
                    )}
                  </p>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
