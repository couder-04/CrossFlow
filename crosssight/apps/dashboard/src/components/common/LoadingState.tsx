export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-12 text-muted text-sm">
      <span className="h-4 w-4 rounded-full border-2 border-accent border-t-transparent animate-spin" />
      {label}
    </div>
  );
}
