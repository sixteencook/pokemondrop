/** Page Catalogue : un produit, toutes ses offres marchandes.
 *
 *  Le dashboard n'affiche plus une ligne par URL, mais une ligne par
 *  PRODUIT — avec le détail de chaque marchand en dessous.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  ChevronDown,
  ChevronRight,
  GitMerge,
  Layers,
  Radar,
  Search,
  Store,
  X,
} from "lucide-react";
import { catalogApi } from "@/api/endpoints";
import type { CatalogProduct } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  PageHeader,
  Pagination,
  Spinner,
  StatCard,
  Tag,
  useToast,
} from "@/components/ui";
import { OfferTable } from "@/components/domain/OfferTable";
import { ProductIdentityPanel } from "@/components/domain/ProductIdentityPanel";
import { formatTimeAgo } from "@/lib/format";
import { ApiError } from "@/api/client";
import { useWsEvent } from "@/ws/WsProvider";

export default function CatalogPage() {
  const queryClient = useQueryClient();
  const { push } = useToast();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const state = useQuery({ queryKey: ["catalog", "state"], queryFn: catalogApi.state });
  const products = useQuery({
    queryKey: ["catalog", "products", page, search],
    queryFn: () =>
      catalogApi.list({ page, page_size: 20, search: search || undefined }),
  });
  const suggestions = useQuery({
    queryKey: ["catalog", "suggestions"],
    queryFn: catalogApi.suggestions,
  });

  useWsEvent("catalog", () => {
    void queryClient.invalidateQueries({ queryKey: ["catalog"] });
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["catalog"] });
  };

  const accept = useMutation({
    mutationFn: (id: number) => catalogApi.acceptSuggestion(id),
    onSuccess: (merged) => {
      push({
        tone: "success",
        title: "Fiches fusionnées",
        description: `${merged.offers.length} offre(s) sur « ${merged.name} »`,
      });
      invalidate();
    },
    onError: (error) =>
      push({
        tone: "danger",
        title: "Fusion impossible",
        description: error instanceof ApiError ? error.message : undefined,
      }),
  });

  const reject = useMutation({
    mutationFn: (id: number) => catalogApi.rejectSuggestion(id),
    onSuccess: () => {
      push({ tone: "info", title: "Rapprochement écarté" });
      invalidate();
    },
  });

  const findOffers = useMutation({
    mutationFn: (uuid: string) => catalogApi.findOffers(uuid),
    onSuccess: (report) => {
      push({
        tone: report.offers_created ? "success" : "info",
        title: report.offers_created
          ? `${report.offers_created} nouvelle(s) offre(s)`
          : "Aucune autre offre trouvée",
        description: report.summary,
      });
      invalidate();
    },
    onError: (error) =>
      push({
        tone: "warning",
        title: "Recherche indisponible",
        description: error instanceof ApiError ? error.message : undefined,
      }),
  });

  const toggle = (uuid: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      next.has(uuid) ? next.delete(uuid) : next.add(uuid);
      return next;
    });

  return (
    <>
      <PageHeader
        title="Catalogue"
        description="Un produit, toutes ses offres marchandes — corrélées automatiquement."
        actions={
          state.data && (
            <Badge tone={state.data.enabled ? "success" : "neutral"} dot
                   pulse={state.data.enabled}>
              {state.data.enabled
                ? `Fusion automatique ≥ ${state.data.merge_threshold}`
                : "Intelligence désactivée"}
            </Badge>
          )
        }
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Produits" tone="accent" icon={<Boxes size={16} />}
                  value={state.data?.products ?? 0}
                  hint="fiches canoniques" />
        <StatCard label="Offres" tone="info" icon={<Store size={16} />}
                  value={state.data?.offers ?? 0}
                  hint="toutes enseignes" />
        <StatCard label="Fusions à valider" tone="warning" icon={<GitMerge size={16} />}
                  value={state.data?.pending_suggestions ?? 0} />
        <StatCard label="Recherches en relance" tone="info" icon={<Radar size={16} />}
                  value={state.data?.pending_retries ?? 0}
                  hint={
                    state.data?.cross_site_search
                      ? state.data.search_capable_sites.join(", ") || "aucun site"
                      : "recherche inter-sites inactive"
                  } />
      </div>

      {/* File de validation des rapprochements */}
      {suggestions.data && suggestions.data.length > 0 && (
        <Card title="Rapprochements à valider" className="mt-4">
          <div className="grid gap-2">
            {suggestions.data.map((suggestion) => (
              <div
                key={suggestion.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-surface-2 px-3 py-2.5"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-text">
                    « {suggestion.product_name} » ↔ « {suggestion.candidate_name} »
                  </p>
                  <p className="mt-0.5 text-[11px] text-muted">
                    {suggestion.reason} · méthode {suggestion.method}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge tone={suggestion.score >= 85 ? "warning" : "neutral"}>
                    confiance {suggestion.score}
                  </Badge>
                  <Button size="sm" variant="primary" icon={<GitMerge size={13} />}
                          loading={accept.isPending}
                          onClick={() => accept.mutate(suggestion.id)}>
                    Fusionner
                  </Button>
                  <Button size="sm" variant="ghost" icon={<X size={13} />}
                          onClick={() => reject.mutate(suggestion.id)} />
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card padded={false} className="mt-4">
        <div className="flex flex-wrap gap-2 border-b border-border p-3">
          <div className="relative">
            <Search size={14}
                    className="absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" />
            <Input className="h-8 w-64 pl-8 text-xs" placeholder="Rechercher un produit…"
                   value={search}
                   onChange={(event) => { setSearch(event.target.value); setPage(1); }} />
          </div>
        </div>

        <div className="p-3">
          {products.isLoading && <Spinner />}
          {products.data && products.data.items.length === 0 && (
            <EmptyState
              icon={<Layers size={24} />}
              title="Catalogue vide"
              description="Les produits apparaîtront dès qu'une fiche sera découverte ou ajoutée."
            />
          )}

          <div className="grid gap-2">
            {products.data?.items.map((product: CatalogProduct) => {
              const open = expanded.has(product.uuid);
              return (
                <article key={product.uuid}
                         className="rounded-lg border border-border bg-surface animate-fade-up">
                  <button
                    onClick={() => toggle(product.uuid)}
                    className="flex w-full items-start gap-3 p-3 text-left"
                  >
                    <span className="mt-1 shrink-0 text-faint">
                      {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                    </span>
                    {product.image_url && (
                      <img src={product.image_url} alt={product.name} loading="lazy"
                           className="size-14 shrink-0 rounded border border-border object-cover" />
                    )}
                    <div className="min-w-0 flex-1">
                      <h3 className="truncate text-sm font-semibold text-text">
                        {product.name}
                      </h3>
                      <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                        {product.brand && <span>{product.brand}</span>}
                        {product.collection && (
                          <><span className="text-faint">·</span><span>{product.collection}</span></>
                        )}
                        {product.release_date && (
                          <><span className="text-faint">·</span><span>sortie {product.release_date}</span></>
                        )}
                        {product.ean && (
                          <><span className="text-faint">·</span>
                           <span className="font-mono">EAN {product.ean}</span></>
                        )}
                      </p>
                      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                        <Badge tone="accent">
                          {product.offers.length} offre(s)
                        </Badge>
                        {product.best_offer_site && (
                          <Badge tone="success" dot pulse>
                            disponible chez {product.best_offer_site}
                          </Badge>
                        )}
                        {product.tags.slice(0, 3).map((tag) => (
                          <Tag key={tag}>{tag}</Tag>
                        ))}
                      </div>
                    </div>
                    <span className="shrink-0 text-[11px] text-faint">
                      {formatTimeAgo(product.updated_at)}
                    </span>
                  </button>

                  {open && (
                    <div className="border-t border-border px-3 pb-3">
                      <div className="py-3">
                        <ProductIdentityPanel productUuid={product.uuid} />
                      </div>
                      <OfferTable offers={product.offers}
                                  bestSite={product.best_offer_site} />
                      <div className="mt-2 flex justify-end">
                        <Button size="sm" variant="secondary" icon={<Radar size={13} />}
                                loading={findOffers.isPending}
                                disabled={!state.data?.cross_site_search}
                                onClick={() => findOffers.mutate(product.uuid)}
                                title={
                                  state.data?.cross_site_search
                                    ? "Chercher ce produit chez les autres marchands"
                                    : "Activez intelligence.cross_site_search"
                                }>
                          Chercher ailleurs
                        </Button>
                      </div>
                    </div>
                  )}
                </article>
              );
            })}
          </div>

          {products.data && (
            <Pagination page={products.data.page} pages={products.data.pages}
                        total={products.data.total} onChange={setPage} />
          )}
        </div>
      </Card>
    </>
  );
}
