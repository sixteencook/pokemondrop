import { Loader2 } from "lucide-react";

export function Spinner({ label = "Chargement…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-muted">
      <Loader2 size={16} className="animate-spin" />
      <span className="text-xs">{label}</span>
    </div>
  );
}
