import { cn } from "@/lib/utils";

export function Card({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("rounded-lg border border-border bg-surface-raised", className)}>
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  action,
}: {
  title: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between px-[var(--card-p)] py-[var(--row-py)] border-b border-border">
      <h3 className="text-sm font-semibold tracking-wide uppercase text-slate-200">{title}</h3>
      {action}
    </div>
  );
}
