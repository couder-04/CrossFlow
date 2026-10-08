import { Suspense } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { LiveView } from "@/components/live/LiveView";

export default function LivePage() {
  return (
    <AppShell>
      <Suspense fallback={null}>
        <LiveView />
      </Suspense>
    </AppShell>
  );
}
