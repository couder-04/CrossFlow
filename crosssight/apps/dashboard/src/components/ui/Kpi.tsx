import { cn } from "@/lib/utils";

const tones = {
  warning: "text-warning",
  danger: "text-danger",
  success: "text-success",
} as const;

export function Kpi({
  label,
  value,
  tone,
  variant = "default",
}: {
  label: string;
  value: string;
  tone?: keyof typeof tones;
  variant?: "default" | "hero";
}) {
  return (
    <div className="rounded border border-border bg-surface-overlay px-3 py-[var(--row-py)]">
      <p className="text-label">{label}</p>
      <p className={cn("mt-1", variant === "hero" ? "text-metric-hero" : "text-metric", tone ? tones[tone] : "text-slate-100")}>
        {value}
      </p>
    </div>
  );
}
