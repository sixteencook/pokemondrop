import type { ReactNode } from "react";
import type { Tone } from "@/lib/format";

interface StatCardProps {
  label: string;
  value: ReactNode;
  icon?: ReactNode;
  hint?: string;
  tone?: Tone;
}

const ICON_TONES: Record<Tone, string> = {
  success: "text-success bg-success/10",
  warning: "text-warning bg-warning/10",
  danger: "text-danger bg-danger/10",
  info: "text-info bg-info/10",
  accent: "text-accent-hover bg-accent/10",
  neutral: "text-muted bg-surface-3",
};

export function StatCard({ label, value, icon, hint, tone = "neutral" }: StatCardProps) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4 animate-fade-up">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs text-muted truncate">{label}</p>
          <p className="mt-1 text-xl font-semibold tracking-tight text-text">{value}</p>
          {hint && <p className="mt-0.5 text-[11px] text-faint truncate">{hint}</p>}
        </div>
        {icon && (
          <div className={`shrink-0 rounded-md p-2 ${ICON_TONES[tone]}`}>{icon}</div>
        )}
      </div>
    </div>
  );
}
