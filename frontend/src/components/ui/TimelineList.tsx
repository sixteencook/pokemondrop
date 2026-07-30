/** Timeline verticale générique : le composant reçoit des items déjà
 *  présentés (label, tone, méta) — aucune connaissance du domaine. */

import type { ReactNode } from "react";
import type { Tone } from "@/lib/format";

export interface TimelineItem {
  id: string | number;
  label: string;
  time: string;
  tone: Tone;
  meta?: ReactNode;
}

const DOT_TONES: Record<Tone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  info: "bg-info",
  accent: "bg-accent",
  neutral: "bg-faint",
};

export function TimelineList({ items }: { items: TimelineItem[] }) {
  return (
    <ol className="relative ml-2 border-l border-border">
      {items.map((item) => (
        <li key={item.id} className="relative mb-4 ml-4 last:mb-0 animate-fade-up">
          <span
            className={`absolute -left-[21.5px] top-1.5 size-2.5 rounded-full ring-4 ring-surface ${DOT_TONES[item.tone]}`}
          />
          <div className="flex flex-wrap items-baseline gap-x-2">
            <p className="text-sm font-medium text-text">{item.label}</p>
            <time className="text-[11px] text-faint">{item.time}</time>
          </div>
          {item.meta && <div className="mt-0.5 text-xs text-muted">{item.meta}</div>}
        </li>
      ))}
    </ol>
  );
}
