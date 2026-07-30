import type { ReactNode } from "react";
import type { Tone } from "@/lib/format";

interface BadgeProps {
  tone?: Tone;
  dot?: boolean;
  pulse?: boolean;
  children: ReactNode;
}

const TONES: Record<Tone, string> = {
  success: "bg-success/10 text-success border-success/25",
  warning: "bg-warning/10 text-warning border-warning/25",
  danger: "bg-danger/10 text-danger border-danger/25",
  info: "bg-info/10 text-info border-info/25",
  accent: "bg-accent/10 text-accent-hover border-accent/25",
  neutral: "bg-surface-3 text-muted border-border",
};

export function Badge({ tone = "neutral", dot = false, pulse = false, children }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5
        text-[11px] font-medium leading-4 whitespace-nowrap ${TONES[tone]}`}
    >
      {dot && (
        <span
          className={`size-1.5 rounded-full bg-current ${pulse ? "animate-pulse-dot" : ""}`}
        />
      )}
      {children}
    </span>
  );
}
