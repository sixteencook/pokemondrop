export function Tag({ children }: { children: string }) {
  return (
    <span className="inline-flex rounded border border-border bg-surface-2 px-1.5 py-0.5 text-[10px] font-medium text-muted">
      {children}
    </span>
  );
}
