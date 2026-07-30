import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
} from "react";

const FIELD_CLASSES = `w-full h-9 rounded-md border border-border bg-surface-2 px-3 text-sm
  text-text placeholder:text-faint transition-colors
  focus:border-accent focus:outline-none`;

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-faint">{hint}</span>}
    </label>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  const { className = "", ...rest } = props;
  return <input className={`${FIELD_CLASSES} ${className}`} {...rest} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  const { className = "", children, ...rest } = props;
  return (
    <select className={`${FIELD_CLASSES} appearance-none ${className}`} {...rest}>
      {children}
    </select>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="inline-flex items-center gap-2"
    >
      <span
        className={`relative h-5 w-9 rounded-full transition-colors duration-200
          ${checked ? "bg-accent" : "bg-surface-3 border border-border"}`}
      >
        <span
          className={`absolute top-0.5 size-4 rounded-full bg-white transition-transform duration-200
            ${checked ? "translate-x-4" : "translate-x-0.5"}`}
        />
      </span>
      {label && <span className="text-sm text-text">{label}</span>}
    </button>
  );
}
