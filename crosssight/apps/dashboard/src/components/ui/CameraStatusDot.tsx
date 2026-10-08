import { cn } from "@/lib/utils";

const tones: Record<string, string> = {
  active: "bg-success ring-success",
  healthy: "bg-success ring-success",
  degraded: "bg-warning ring-warning",
  stale: "bg-slate-400 ring-slate-400",
  offline: "bg-slate-400 ring-slate-400",
};

/** 8×8 status dot. Colors follow `cameraStatusColor()` (offline stays slate). */
export function CameraStatusDot({ status, className }: { status: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-block h-2 w-2 rounded-full ring-2 ring-offset-1 ring-offset-surface",
        tones[status] ?? "bg-sky-400 ring-sky-400",
        className,
      )}
      aria-hidden
    />
  );
}
