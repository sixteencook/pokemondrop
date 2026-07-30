/** Miniature de capture d'écran (page Alertes). */

import { ImageOff } from "lucide-react";
import type { Alert } from "@/api/types";

interface ScreenshotThumbnailProps {
  alert: Alert;
  onOpen: (alert: Alert) => void;
}

export function ScreenshotThumbnail({ alert, onOpen }: ScreenshotThumbnailProps) {
  if (!alert.screenshot_url) {
    return (
      <span
        className="flex size-11 items-center justify-center rounded border border-border bg-surface-2 text-faint"
        title="Aucune capture pour cette alerte"
      >
        <ImageOff size={14} />
      </span>
    );
  }
  return (
    <button
      onClick={() => onOpen(alert)}
      className="group block size-11 overflow-hidden rounded border border-border transition-colors hover:border-accent"
      title="Agrandir la capture"
    >
      <img
        src={alert.screenshot_url}
        alt={`Capture — ${alert.product_name ?? "produit"}`}
        loading="lazy"
        className="size-full object-cover object-top transition-transform duration-200 group-hover:scale-105"
      />
    </button>
  );
}
