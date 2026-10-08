import { Suspense } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { AlertsView } from "@/components/alerts/AlertsView";

export default function AlertsPage() {
  return (
    <AppShell>
      <Suspense fallback={null}>
        <AlertsView />
      </Suspense>
    </AppShell>
  );
}
