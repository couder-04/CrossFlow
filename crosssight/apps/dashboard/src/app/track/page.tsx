import { Suspense } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { TrackView } from "@/components/track/TrackView";

export default function TrackPage() {
  return (
    <AppShell>
      <Suspense fallback={null}>
        <TrackView />
      </Suspense>
    </AppShell>
  );
}
