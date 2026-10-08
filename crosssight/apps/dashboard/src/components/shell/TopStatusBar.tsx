"use client";

import { Bell, Menu } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { formatReadsRate, useReadsRate } from "@/lib/hooks/useReadsRate";
import { api } from "@/lib/api";
import { formatDualClock, cn } from "@/lib/utils";
import { LiveSocket, type SocketState } from "@/lib/ws";

type SystemState = "healthy" | "degraded" | "down";

export function TopStatusBar({
  unread,
  onToggleTray,
  onOpenNav,
}: {
  unread: number;
  onToggleTray: () => void;
  onOpenNav: () => void;
}) {
  const router = useRouter();
  const readsRate = useReadsRate();
  const [system, setSystem] = useState<SystemState>("degraded");
  const [cameras, setCameras] = useState({ healthy: 0, total: 0 });
  const [socketState, setSocketState] = useState<SocketState>("reconnecting");
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    setNow(new Date());
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function probe() {
      let live = false;
      try {
        const response = await fetch("/api/auth/me");
        live = response.ok;
      } catch {
        live = false;
      }
      let rows: { status: string }[] = [];
      let reachable = true;
      try {
        rows = await api.cameras();
      } catch {
        reachable = false;
      }
      if (cancelled) return;
      if (!live || !reachable) {
        setSystem("down");
        return;
      }
      const healthy = rows.filter((camera) => camera.status === "active" || camera.status === "healthy").length;
      setCameras({ healthy, total: rows.length });
      if (rows.length === 0 || healthy < rows.length) setSystem("degraded");
      else setSystem("healthy");
    }
    probe();
    const timer = window.setInterval(probe, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const socket = new LiveSocket();
    socket.connect();
    const off = socket.onState(setSocketState);
    return () => {
      off();
      socket.close();
    };
  }, []);

  const clock = now ? formatDualClock(now) : null;
  const systemTone = system === "healthy" ? "text-success" : system === "degraded" ? "text-warning" : "text-danger";
  const socketTone = socketState === "live" ? "text-success" : socketState === "reconnecting" ? "text-warning" : "text-danger";

  return (
    <header className="no-print h-9 shrink-0 border-b border-border bg-surface-raised px-3 flex items-center gap-3 text-[11px] font-mono">
      <button
        type="button"
        className="md:hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
        aria-label="Open navigation"
        onClick={onOpenNav}
      >
        <Menu size={14} />
      </button>
      <span className="inline-flex items-center gap-1.5">
        <span className={cn("h-2 w-2 rounded-full", system === "healthy" && "bg-success animate-pulse", system === "degraded" && "bg-warning", system === "down" && "bg-danger")} />
        <span className={systemTone}>{system === "healthy" ? "SYSTEM: LIVE" : system === "degraded" ? "SYSTEM: DEGRADED" : "SYSTEM: DOWN"}</span>
      </span>
      <button
        type="button"
        className="text-slate-200 hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
        onClick={() => router.push("/health")}
      >
        {cameras.healthy} / {cameras.total} cameras
      </button>
      <span className="text-muted">{formatReadsRate(readsRate)}</span>
      <span className={cn(socketTone, socketState === "reconnecting" && "animate-pulse")}>
        {socketState === "live" ? "LIVE" : socketState === "reconnecting" ? "RECONNECTING" : "OFFLINE"}
      </span>
      <button
        type="button"
        className="relative ml-auto mr-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
        aria-label="Open incident tray"
        onClick={onToggleTray}
      >
        <Bell size={14} />
        {unread > 0 && (
          <span className="absolute -top-2 -right-2 min-w-4 rounded-full bg-danger px-1 text-[9px] leading-4 text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      <span className="text-muted">{clock ? `IST ${clock.ist} · UTC ${clock.utc}` : "IST --:-- · UTC --:--"}</span>
    </header>
  );
}
