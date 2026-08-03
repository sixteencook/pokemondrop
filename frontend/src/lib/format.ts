/** Formatage et libellés partagés (aucune logique métier, présentation pure). */

import type { Availability, Priority } from "@/api/types";

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return date.toLocaleString("fr-FR", {
    day: "2-digit", month: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

export function formatTimeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return `il y a ${Math.round(seconds)} s`;
  if (seconds < 3600) return `il y a ${Math.round(seconds / 60)} min`;
  if (seconds < 86400) return `il y a ${Math.round(seconds / 3600)} h`;
  return `il y a ${Math.round(seconds / 86400)} j`;
}

export function formatDuration(totalSeconds: number): string {
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  if (days > 0) return `${days} j ${hours} h`;
  if (hours > 0) return `${hours} h ${minutes} min`;
  return `${minutes} min`;
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return `${Math.round(ms)} ms`;
}

export type Tone = "success" | "warning" | "danger" | "info" | "neutral" | "accent";

export const AVAILABILITY_META: Record<
  Availability | "error",
  { label: string; tone: Tone }
> = {
  in_stock: { label: "Disponible", tone: "success" },
  preorder: { label: "Précommande", tone: "info" },
  unavailable: { label: "Indisponible", tone: "warning" },
  not_listed: { label: "Non publié", tone: "neutral" },
  unknown: { label: "Inconnu", tone: "neutral" },
  error: { label: "Erreur", tone: "danger" },
};

export const PRIORITY_META: Record<Priority, { label: string; tone: Tone }> = {
  low: { label: "Basse", tone: "neutral" },
  normal: { label: "Normale", tone: "info" },
  high: { label: "Haute", tone: "warning" },
  critical: { label: "Critique", tone: "danger" },
};

export const EVENT_TYPE_META: Record<string, { tone: Tone }> = {
  baseline: { tone: "neutral" },
  unstable: { tone: "warning" },
  discovered: { tone: "accent" },
  product_appeared: { tone: "accent" },
  price_appeared: { tone: "info" },
  price_changed: { tone: "info" },
  preorder_opened: { tone: "success" },
  invitation_opened: { tone: "success" },
  back_in_stock: { tone: "success" },
  went_out_of_stock: { tone: "danger" },
  product_delisted: { tone: "neutral" },
  seller_became_official: { tone: "accent" },
  seller_left_buybox: { tone: "warning" },
  status_changed: { tone: "warning" },
  // Événements hérités : plus jamais produits, conservés pour l'historique.
  button_changed: { tone: "neutral" },
  page_changed: { tone: "neutral" },
};

export const LOG_LEVEL_TONE: Record<string, Tone> = {
  INFO: "info",
  CHECK: "neutral",
  WARN: "warning",
  ALERTE: "success",
  ERROR: "danger",
};
