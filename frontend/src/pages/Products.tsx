/** Gestion des produits : CRUD complet, filtres, pagination. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PackagePlus, Search, Trash2 } from "lucide-react";
import { monitorsApi, productsApi } from "@/api/endpoints";
import type { Product, ProductInput } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Modal,
  PageHeader,
  Pagination,
  Select,
  Spinner,
  Table,
  Tag,
  Toggle,
  useToast,
} from "@/components/ui";
import type { Column } from "@/components/ui/Table";
import { AvailabilityBadge } from "@/components/domain/AvailabilityBadge";
import { ProductFormModal } from "@/components/domain/ProductFormModal";
import { ProductTimelineModal } from "@/components/domain/ProductTimelineModal";
import { PRIORITY_META, formatTimeAgo } from "@/lib/format";
import { ApiError } from "@/api/client";

export default function ProductsPage() {
  const queryClient = useQueryClient();
  const { push } = useToast();

  const [page, setPage] = useState(1);
  const [site, setSite] = useState("");
  const [enabledFilter, setEnabledFilter] = useState("");
  const [search, setSearch] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Product | null>(null);
  const [deleting, setDeleting] = useState<Product | null>(null);
  const [historyOf, setHistoryOf] = useState<Product | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const monitors = useQuery({ queryKey: ["monitors"], queryFn: monitorsApi.list });
  const products = useQuery({
    queryKey: ["products", "list", page, site, enabledFilter, search],
    queryFn: () =>
      productsApi.list({
        page,
        page_size: 20,
        sort: "created_at",
        order: "asc",
        site: site || undefined,
        enabled: enabledFilter || undefined,
        search: search || undefined,
      }),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["products"] });
    void queryClient.invalidateQueries({ queryKey: ["stats"] });
  };

  const save = useMutation({
    mutationFn: (values: ProductInput) =>
      editing ? productsApi.update(editing.uuid, values) : productsApi.create(values),
    onSuccess: (_, values) => {
      push({
        tone: "success",
        title: editing ? "Produit modifié" : "Produit ajouté",
        description: `${values.name} — pris en compte à chaud, sans redémarrage.`,
      });
      setFormOpen(false);
      setEditing(null);
      setFormError(null);
      invalidate();
    },
    onError: (error) =>
      setFormError(error instanceof ApiError ? error.message : "Enregistrement impossible."),
  });

  const toggle = useMutation({
    mutationFn: (product: Product) =>
      productsApi.update(product.uuid, { enabled: !product.enabled }),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: (product: Product) => productsApi.remove(product.uuid),
    onSuccess: (_, product) => {
      push({ tone: "info", title: "Produit supprimé", description: product.name });
      setDeleting(null);
      invalidate();
    },
  });

  const columns: Column<Product>[] = [
    {
      key: "name",
      header: "Produit",
      render: (product) => (
        <div className="min-w-0">
          <button
            className="block truncate text-left font-medium text-text hover:text-accent-hover"
            onClick={() => setHistoryOf(product)}
            title="Voir l'historique"
          >
            {product.name}
          </button>
          <div className="mt-0.5 flex flex-wrap gap-1">
            {product.tags.slice(0, 3).map((tag) => <Tag key={tag}>{tag}</Tag>)}
          </div>
        </div>
      ),
    },
    {
      key: "site",
      header: "Site",
      render: (product) => <span className="capitalize text-muted">{product.site}</span>,
    },
    {
      key: "status",
      header: "Statut",
      render: (product) => (
        <AvailabilityBadge availability={product.availability}
                           monitorable={product.monitorable} />
      ),
    },
    {
      key: "priority",
      header: "Priorité",
      render: (product) => {
        const meta = PRIORITY_META[product.priority];
        return <Badge tone={meta.tone}>{meta.label}</Badge>;
      },
    },
    {
      key: "interval",
      header: "Intervalle",
      render: (product) => <span className="text-muted">{product.check_interval} s</span>,
    },
    {
      key: "checked",
      header: "Dernier check",
      render: (product) => (
        <span className="text-muted">{formatTimeAgo(product.last_checked_at)}</span>
      ),
    },
    {
      key: "enabled",
      header: "Actif",
      render: (product) => (
        <Toggle checked={product.enabled} onChange={() => toggle.mutate(product)} />
      ),
    },
    {
      key: "actions",
      header: "",
      className: "text-right",
      render: (product) => (
        <div className="flex justify-end gap-1">
          <Button size="sm" variant="ghost"
                  onClick={() => { setEditing(product); setFormError(null); setFormOpen(true); }}>
            Modifier
          </Button>
          <Button size="sm" variant="ghost" icon={<Trash2 size={13} />}
                  onClick={() => setDeleting(product)} />
        </div>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Produits"
        description="Ajout, modification et suppression — appliqués à chaud par le moteur."
        actions={
          <Button variant="primary" icon={<PackagePlus size={15} />}
                  onClick={() => { setEditing(null); setFormError(null); setFormOpen(true); }}>
            Ajouter un produit
          </Button>
        }
      />

      <Card padded={false}>
        {/* Filtres */}
        <div className="flex flex-wrap gap-2 border-b border-border p-3">
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" />
            <Input
              className="h-8 w-56 pl-8 text-xs"
              placeholder="Rechercher…"
              value={search}
              onChange={(event) => { setSearch(event.target.value); setPage(1); }}
            />
          </div>
          <Select className="h-8 w-36 text-xs" value={site}
                  onChange={(event) => { setSite(event.target.value); setPage(1); }}>
            <option value="">Tous les sites</option>
            {monitors.data?.map((monitor) => (
              <option key={monitor.site} value={monitor.site}>{monitor.display_name}</option>
            ))}
          </Select>
          <Select className="h-8 w-36 text-xs" value={enabledFilter}
                  onChange={(event) => { setEnabledFilter(event.target.value); setPage(1); }}>
            <option value="">Tous les états</option>
            <option value="true">Activés</option>
            <option value="false">Désactivés</option>
          </Select>
        </div>

        {products.isLoading ? (
          <Spinner />
        ) : (
          <>
            <Table
              columns={columns}
              rows={products.data?.items ?? []}
              rowKey={(product) => product.uuid}
              empty={
                <EmptyState
                  icon={<PackagePlus size={24} />}
                  title="Aucun produit trouvé"
                  description="Ajustez les filtres ou ajoutez un produit."
                />
              }
            />
            {products.data && (
              <Pagination page={products.data.page} pages={products.data.pages}
                          total={products.data.total} onChange={setPage} />
            )}
          </>
        )}
      </Card>

      <ProductFormModal
        open={formOpen}
        product={editing}
        sites={monitors.data?.map((monitor) => monitor.site) ?? ["micromania"]}
        saving={save.isPending}
        error={formError}
        onClose={() => { setFormOpen(false); setEditing(null); }}
        onSubmit={(values) => save.mutate(values)}
      />

      <Modal
        open={deleting !== null}
        title="Supprimer le produit"
        onClose={() => setDeleting(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>Annuler</Button>
            <Button variant="danger" loading={remove.isPending}
                    onClick={() => deleting && remove.mutate(deleting)}>
              Supprimer définitivement
            </Button>
          </>
        }
      >
        <p className="text-sm text-muted">
          Supprimer <span className="font-medium text-text">{deleting?.name}</span> ?
          Tout son historique (checks, timeline, alertes) sera également effacé.
        </p>
      </Modal>

      <ProductTimelineModal product={historyOf} onClose={() => setHistoryOf(null)} />
    </>
  );
}
