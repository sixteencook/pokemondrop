/** Historique des alertes, filtrable. */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BellOff, ExternalLink, FileSearch } from "lucide-react";
import { alertsApi, monitorsApi } from "@/api/endpoints";
import type { Alert } from "@/api/types";
import {
  Badge,
  Card,
  EmptyState,
  Lightbox,
  PageHeader,
  Pagination,
  Select,
  Spinner,
  Table,
} from "@/components/ui";
import type { Column } from "@/components/ui/Table";
import { ScreenshotThumbnail } from "@/components/domain/ScreenshotThumbnail";
import { EVENT_TYPE_META, formatDateTime } from "@/lib/format";
import { useWsEvent } from "@/ws/WsProvider";

// Événements métier uniquement : chacun répond à « qu'est-ce qui a changé
// pour l'acheteur ? ». Les anciens « Bouton modifié » / « Page modifiée »
// ne sont plus produits par le moteur.
const CHANGE_TYPES = [
  ["preorder_opened", "Précommande ouverte"],
  ["invitation_opened", "Invitation ouverte"],
  ["back_in_stock", "Retour en stock"],
  ["went_out_of_stock", "Rupture de stock"],
  ["seller_became_official", "Vendeur officiel de retour"],
  ["seller_left_buybox", "Vendeur officiel absent"],
  ["product_appeared", "Produit découvert"],
  ["product_delisted", "Fiche retirée"],
  ["price_appeared", "Prix détecté"],
  ["price_changed", "Prix modifié"],
  ["status_changed", "Disponibilité modifiée"],
] as const;

const LABELS = Object.fromEntries(CHANGE_TYPES) as Record<string, string>;

export default function AlertsPage() {
  const [page, setPage] = useState(1);
  const [site, setSite] = useState("");
  const [changeType, setChangeType] = useState("");
  const [preview, setPreview] = useState<Alert | null>(null);

  const monitors = useQuery({ queryKey: ["monitors"], queryFn: monitorsApi.list });
  const alerts = useQuery({
    queryKey: ["alerts", page, site, changeType],
    queryFn: () =>
      alertsApi.list({
        page,
        page_size: 25,
        site: site || undefined,
        change_type: changeType || undefined,
      }),
  });

  // Une capture vient d'être produite : la miniature apparaît sans rechargement.
  useWsEvent("screenshot", () => void alerts.refetch());

  const columns: Column<Alert>[] = [
    {
      key: "screenshot",
      header: "Capture",
      render: (alert) => <ScreenshotThumbnail alert={alert} onOpen={setPreview} />,
    },
    {
      key: "date",
      header: "Date",
      render: (alert) => (
        <span className="whitespace-nowrap text-muted">{formatDateTime(alert.created_at)}</span>
      ),
    },
    {
      key: "product",
      header: "Produit",
      render: (alert) => (
        <span className="font-medium text-text">
          {alert.product_name ?? <em className="text-faint">produit supprimé</em>}
        </span>
      ),
    },
    {
      key: "site",
      header: "Site",
      render: (alert) => <span className="capitalize text-muted">{alert.site ?? "—"}</span>,
    },
    {
      key: "type",
      header: "Événement",
      render: (alert) => (
        <Badge tone={EVENT_TYPE_META[alert.change_type]?.tone ?? "neutral"}>
          {LABELS[alert.change_type] ?? alert.change_type}
        </Badge>
      ),
    },
    {
      key: "transition",
      header: "Changement",
      render: (alert) => (
        <span className="text-xs text-muted">
          {alert.old_value ?? "—"} → {alert.new_value ?? "—"}
          {alert.price && <span className="ml-1.5 text-info">{alert.price}</span>}
        </span>
      ),
    },
    {
      key: "notified",
      header: "Telegram",
      render: (alert) =>
        alert.notified ? (
          <Badge tone="success">Envoyée</Badge>
        ) : (
          <Badge tone="neutral">Non envoyée</Badge>
        ),
    },
    {
      key: "link",
      header: "",
      render: (alert) => (
        <div className="flex items-center justify-end gap-1.5">
          {alert.evidence_url && (
            <a
              href={alert.evidence_url}
              target="_blank"
              rel="noreferrer"
              className="text-muted transition-colors hover:text-accent-hover"
              title="Voir la page analysée au moment de l'alerte"
            >
              <FileSearch size={14} />
            </a>
          )}
          {alert.url && (
            <a href={alert.url} target="_blank" rel="noreferrer"
               className="text-muted transition-colors hover:text-accent-hover"
               title="Ouvrir la fiche">
              <ExternalLink size={14} />
            </a>
          )}
        </div>
      ),
    },
  ];

  return (
    <>
      <PageHeader title="Alertes" description="Toutes les alertes détectées et envoyées." />
      <Card padded={false}>
        <div className="flex flex-wrap gap-2 border-b border-border p-3">
          <Select className="h-8 w-40 text-xs" value={site}
                  onChange={(event) => { setSite(event.target.value); setPage(1); }}>
            <option value="">Tous les sites</option>
            {monitors.data?.map((monitor) => (
              <option key={monitor.site} value={monitor.site}>{monitor.display_name}</option>
            ))}
          </Select>
          <Select className="h-8 w-48 text-xs" value={changeType}
                  onChange={(event) => { setChangeType(event.target.value); setPage(1); }}>
            <option value="">Tous les événements</option>
            {CHANGE_TYPES.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </Select>
        </div>

        {alerts.isLoading ? (
          <Spinner />
        ) : (
          <>
            <Table
              columns={columns}
              rows={alerts.data?.items ?? []}
              rowKey={(alert) => alert.id}
              empty={
                <EmptyState
                  icon={<BellOff size={24} />}
                  title="Aucune alerte"
                  description="Les alertes apparaîtront ici dès qu'un changement sera détecté."
                />
              }
            />
            {alerts.data && (
              <Pagination page={alerts.data.page} pages={alerts.data.pages}
                          total={alerts.data.total} onChange={setPage} />
            )}
          </>
        )}
      </Card>

      <Lightbox
        open={preview !== null}
        src={preview?.screenshot_url ?? null}
        title={preview?.product_name ?? "Capture d'écran"}
        subtitle={
          preview
            ? `${preview.site ?? "—"} · ${formatDateTime(preview.created_at)}`
            : undefined
        }
        downloadUrl={
          preview?.screenshot_url ? `${preview.screenshot_url}?download=true` : null
        }
        linkUrl={preview?.url || null}
        onClose={() => setPreview(null)}
      />
    </>
  );
}
