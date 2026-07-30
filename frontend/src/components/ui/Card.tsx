import type { ReactNode } from "react";

interface CardProps {
  title?: ReactNode;
  actions?: ReactNode;
  padded?: boolean;
  className?: string;
  children: ReactNode;
}

export function Card({ title, actions, padded = true, className = "", children }: CardProps) {
  return (
    <section
      className={`rounded-lg border border-border bg-surface animate-fade-up ${className}`}
    >
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <h2 className="text-sm font-medium text-text">{title}</h2>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={padded ? "p-4" : ""}>{children}</div>
    </section>
  );
}
