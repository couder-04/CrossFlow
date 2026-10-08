import { cn } from "@/lib/utils";

const tones: Record<string, string> = {
  default: "bg-surface-overlay text-slate-200",
  success: "bg-success/20 text-success",
  warning: "bg-warning/20 text-warning",
  danger: "bg-danger/20 text-danger",
  accent: "bg-accent/20 text-accent",
};

export function Badge({
  children,
  tone = "default",
  case: textCase,
  className,
}: {
  children: React.ReactNode;
  tone?: keyof typeof tones;
  case?: "upper" | "normal";
  className?: string;
}) {
  const letterCase = textCase ?? "upper";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-2 py-0.5 text-xs font-mono tracking-wide",
        letterCase === "normal" ? "normal-case" : "uppercase",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
