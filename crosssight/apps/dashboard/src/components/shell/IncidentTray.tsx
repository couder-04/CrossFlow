"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AlertTypeIcon } from "@/components/ui/AlertTypeIcon";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { api } from "@/lib/api";
import { autoOpenTrayFor, getPrefs } from "@/lib/prefs";
import { playCue } from "@/lib/sounds";
import { toast } from "@/lib/toast";
import { cn, formatRelative, severityRank } from "@/lib/utils";
import { LiveSocket } from "@/lib/ws";
import type { Alert, Role } from "@/types";

export function IncidentTray({
  open,
  onOpenChange,
  onUnread,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onUnread: (count: number) => void;
}) {
  const router = useRouter();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [pulsing, setPulsing] = useState<Set<string>>(new Set());
  const [role, setRole] = useState<Role | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    fetch("/api/auth/me")
      .then((response) => (response.ok ? response.json() : null))
      .then((user: { role?: Role } | null) => setRole(user?.role ?? null))
      .catch(() => setRole(null));
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    api.alerts().then(setAlerts).catch(() => undefined);
  }, []);

  useEffect(() => {
    const socket = new LiveSocket();
    socket.connect();
    socket.subscribe(["alerts"]);
    const off = socket.onMessage((msg) => {
      if (msg.channel !== "alerts") return;
      const alert = msg.data as Alert;
      setAlerts((prev) => {
        const exists = alert.id && prev.some((row) => row.id === alert.id);
        if (exists) return prev.map((row) => (row.id === alert.id ? { ...row, ...alert } : row));
        return [alert, ...prev];
      });
      if (alert.severity === "critical" || alert.severity === "high") {
        playCue(alert.severity === "critical" ? "alert-critical" : "alert-high");
        if (autoOpenTrayFor(role, getPrefs())) onOpenChange(true);
      }
      if (alert.severity === "critical" && alert.id) {
        const id = alert.id;
        setPulsing((prev) => new Set(prev).add(id));
        window.setTimeout(() => {
          setPulsing((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
        }, 3000);
      }
    });
    return () => {
      off();
      socket.close();
    };
  }, [onOpenChange, role]);

  const active = alerts
    .filter((alert) => (alert.status ?? "new") === "new")
    .sort((a, b) => severityRank(a.severity) - severityRank(b.severity) || Date.parse(a.ts ?? "") - Date.parse(b.ts ?? ""));
  const inReview = alerts.filter((alert) => alert.status === "reviewing" || alert.status === "dispatched");

  useEffect(() => {
    onUnread(active.length);
  }, [active.length, onUnread]);

  async function ack(id: string) {
    try {
      const updated = await api.ackAlert(id);
      setAlerts((prev) => prev.map((alert) => (alert.id === id ? updated : alert)));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to acknowledge alert");
    }
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.aside
          className="no-print fixed top-9 right-0 z-40 h-[calc(100%-36px)] w-[380px] max-w-full border-l border-border bg-surface-raised flex flex-col"
          initial={{ x: 380 }}
          animate={{ x: 0 }}
          exit={{ x: 380 }}
          transition={{ type: "tween", duration: 0.2 }}
        >
          <div className="flex items-center justify-between px-3 py-2 border-b border-border">
            <h2 className="text-label">Incidents</h2>
            <Button variant="ghost" size="sm" aria-label="Close incident tray" onClick={() => onOpenChange(false)}>
              Close
            </Button>
          </div>
          <div className="flex-1 overflow-y-auto p-3 space-y-3">
            <section>
              <h3 className="text-label mb-2">Active</h3>
              <div className="space-y-2">
                <AnimatePresence initial={false}>
                  {active.map((alert) => (
                    <AlertRow
                      key={alert.id ?? `${alert.plate_norm}-${alert.ts}`}
                      alert={alert}
                      pulse={Boolean(alert.id && pulsing.has(alert.id))}
                      now={now}
                      onAck={() => alert.id && ack(alert.id)}
                      onOpen={() => alert.id && router.push(`/alerts?id=${encodeURIComponent(alert.id)}`)}
                    />
                  ))}
                </AnimatePresence>
                {active.length === 0 && <p className="text-xs text-muted">No unacknowledged alerts.</p>}
              </div>
            </section>
            <section>
              <button type="button" className="text-label mb-2" onClick={() => setReviewOpen((value) => !value)}>
                In review ({inReview.length})
              </button>
              {reviewOpen && (
                <div className="space-y-2">
                  {inReview.map((alert) => (
                    <AlertRow
                      key={alert.id ?? `${alert.plate_norm}-${alert.ts}`}
                      alert={alert}
                      now={now}
                      onOpen={() => alert.id && router.push(`/alerts?id=${encodeURIComponent(alert.id)}`)}
                    />
                  ))}
                </div>
              )}
            </section>
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

function AlertRow({
  alert,
  pulse,
  now,
  onAck,
  onOpen,
}: {
  alert: Alert;
  pulse?: boolean;
  now: number;
  onAck?: () => void;
  onOpen: () => void;
}) {
  const tone = alert.severity === "critical" || alert.severity === "high" ? "danger" : "warning";
  const iconTone = tone === "danger" ? "text-danger" : "text-warning";
  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: 40 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 40 }}
      className={cn(
        "rounded border p-2 text-xs",
        alert.severity === "critical" ? "card-critical" : alert.severity === "high" ? "card-warning" : "border-border bg-surface-overlay",
        pulse && "pulse-critical",
      )}
    >
      <div className="flex items-center gap-2">
        <Badge tone={tone}>{alert.severity}</Badge>
        <AlertTypeIcon type={alert.type} className={iconTone} />
        <span className="font-mono text-accent">{alert.plate_norm}</span>
        <span className="ml-auto text-muted">{alert.ts && now ? formatRelative(alert.ts) : ""}</span>
      </div>
      <p className="mt-1 text-muted font-mono">{alert.camera_ids?.[0] ?? "—"}</p>
      <div className="mt-2 flex gap-2">
        {onAck && (
          <Button size="sm" variant="secondary" onClick={onAck}>
            Ack
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={onOpen}>
          Open
        </Button>
      </div>
    </motion.div>
  );
}
