/** Visionneuse plein écran d'une image — générique, sans logique métier. */

import { useEffect } from "react";
import { createPortal } from "react-dom";
import { Download, ExternalLink, X } from "lucide-react";

interface LightboxProps {
  open: boolean;
  src: string | null;
  title?: string;
  subtitle?: string;
  downloadUrl?: string | null;
  linkUrl?: string | null;
  onClose: () => void;
}

export function Lightbox({
  open,
  src,
  title,
  subtitle,
  downloadUrl,
  linkUrl,
  onClose,
}: LightboxProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || !src) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[70] flex flex-col bg-black/85 backdrop-blur-sm"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <header className="flex items-center justify-between gap-3 border-b border-border/60 px-4 py-2.5">
        <div className="min-w-0">
          {title && <p className="truncate text-sm font-medium text-text">{title}</p>}
          {subtitle && <p className="truncate text-xs text-muted">{subtitle}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {linkUrl && (
            <a
              href={linkUrl}
              target="_blank"
              rel="noreferrer"
              className="rounded-md p-1.5 text-muted transition-colors hover:bg-surface-2 hover:text-text"
              title="Ouvrir la fiche produit"
            >
              <ExternalLink size={16} />
            </a>
          )}
          {downloadUrl && (
            <a
              href={downloadUrl}
              className="rounded-md p-1.5 text-muted transition-colors hover:bg-surface-2 hover:text-text"
              title="Télécharger la capture"
            >
              <Download size={16} />
            </a>
          )}
          <button
            onClick={onClose}
            className="rounded-md p-1.5 text-muted transition-colors hover:bg-surface-2 hover:text-text"
            aria-label="Fermer"
          >
            <X size={16} />
          </button>
        </div>
      </header>
      <div className="flex-1 overflow-auto p-4">
        <img
          src={src}
          alt={title ?? "Capture d'écran"}
          className="mx-auto max-w-full rounded-lg border border-border shadow-2xl animate-fade-up"
        />
      </div>
    </div>,
    document.body
  );
}
