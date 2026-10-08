"use client";

import { Command } from "cmdk";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { navIcons } from "@/components/ui/icons";
import { api } from "@/lib/api";
import { navItemsForRole } from "@/lib/auth";
import { isPlate, normalizePlate } from "@/lib/plates";
import type { Alert, Camera, Role } from "@/types";

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [role, setRole] = useState<Role>("analyst");
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    let cancelled = false;
    fetch("/api/auth/me")
      .then((response) => (response.ok ? response.json() : null))
      .then((user: { role?: Role } | null) => {
        if (!cancelled && user?.role) setRole(user.role);
      })
      .catch(() => undefined);
    api.cameras().then((rows) => { if (!cancelled) setCameras(rows); }).catch(() => undefined);
    api.alerts({ limit: "20" }).then((rows) => { if (!cancelled) setAlerts(rows.slice(0, 20)); }).catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [open]);

  function go(href: string) {
    onOpenChange(false);
    router.push(href);
  }

  if (!open) return null;

  const plate = normalizePlate(query);
  const pages = navItemsForRole(role);

  return (
    <div className="no-print fixed inset-0 z-50 bg-black/50" onClick={() => onOpenChange(false)}>
      <div
        className="absolute left-1/2 top-[20%] w-[640px] max-w-[calc(100%-2rem)] -translate-x-1/2"
        onClick={(event) => event.stopPropagation()}
      >
        <Command
          className="overflow-hidden rounded-lg border border-border bg-surface-raised shadow-xl"
          label="Command palette"
          shouldFilter
        >
          <Command.Input
            autoFocus
            value={query}
            onValueChange={setQuery}
            placeholder="Search pages, cameras, alerts, plates"
            className="border-b border-border"
          />
          <Command.List className="max-h-80 overflow-y-auto p-2">
            <Command.Empty className="text-label px-2 py-3">↑↓ navigate  ⏎ open  esc close</Command.Empty>
            <Command.Group heading="Pages">
              {pages.map((item) => {
                const Icon = navIcons[item.icon];
                return (
                  <Command.Item key={item.href} value={`${item.label} ${item.href}`} onSelect={() => go(item.href)}>
                    <Icon size={14} aria-hidden />
                    <span>{item.label}</span>
                    <span className="ml-auto text-xs text-muted font-mono">{item.href}</span>
                  </Command.Item>
                );
              })}
            </Command.Group>
            <Command.Group heading="Cameras">
              {cameras.map((camera) => (
                <Command.Item
                  key={camera.id}
                  value={`${camera.id} ${camera.name}`}
                  onSelect={() => go(`/live?focus=${encodeURIComponent(camera.id)}`)}
                >
                  <span className="font-mono text-xs">{camera.id}</span>
                  <span>{camera.name}</span>
                </Command.Item>
              ))}
            </Command.Group>
            <Command.Group heading="Recent alerts">
              {alerts.map((alert) => (
                <Command.Item
                  key={alert.id ?? `${alert.plate_norm}-${alert.ts}`}
                  value={`${alert.plate_norm} ${alert.type} ${alert.id ?? ""}`}
                  onSelect={() => alert.id && go(`/alerts?id=${encodeURIComponent(alert.id)}`)}
                >
                  <span className="font-mono text-accent">{alert.plate_norm}</span>
                  <span className="text-muted">{alert.type.replace(/_/g, " ")}</span>
                </Command.Item>
              ))}
            </Command.Group>
            {isPlate(query) && (
              <Command.Group heading="Plate lookup">
                <Command.Item value={`investigate plate ${plate}`} onSelect={() => go(`/track?plate=${encodeURIComponent(plate)}`)}>
                  Investigate plate {plate}
                </Command.Item>
              </Command.Group>
            )}
          </Command.List>
          <div className="text-label border-t border-border px-3 py-2">↑↓ navigate  ⏎ open  esc close</div>
        </Command>
      </div>
    </div>
  );
}
