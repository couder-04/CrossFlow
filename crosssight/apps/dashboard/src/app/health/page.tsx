import { AppShell } from "@/components/layout/AppShell";
import { HealthPanel } from "@/components/ops/Panels";

export default function HealthPage() {
  return (
    <AppShell>
      <HealthPanel />
    </AppShell>
  );
}
