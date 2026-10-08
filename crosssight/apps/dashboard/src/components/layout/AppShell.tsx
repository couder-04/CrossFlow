"use client";

import { formatDistanceToNow } from "date-fns";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { Sidebar } from "@/components/shell/Sidebar";
import { TopStatusBar } from "@/components/shell/TopStatusBar";
import { getPrefs } from "@/lib/prefs";
import { unlockAudio } from "@/lib/sounds";
import { toast } from "@/lib/toast";
import { Toaster } from "sonner";

const CommandPalette = dynamic(
  () => import("@/components/shell/CommandPalette").then((mod) => mod.CommandPalette),
  { ssr: false },
);
const IncidentTray = dynamic(
  () => import("@/components/shell/IncidentTray").then((mod) => mod.IncidentTray),
  { ssr: false },
);

export function AppShell({ children }: { children: React.ReactNode }) {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [trayOpen, setTrayOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    document.documentElement.dataset.density = getPrefs().density;
    const unlock = () => unlockAudio();
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", unlock);
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, []);

  useEffect(() => {
    const raw = sessionStorage.getItem("crosssight:last-login");
    if (!raw) return;
    sessionStorage.removeItem("crosssight:last-login");
    try {
      const data = JSON.parse(raw) as { last_login_at?: string | null; last_login_ip?: string | null };
      if (!data.last_login_at) return;
      const when = formatDistanceToNow(new Date(data.last_login_at), { addSuffix: true });
      const from = data.last_login_ip ? ` from ${data.last_login_ip}` : "";
      toast.success(`Last sign-in: ${when}${from}`);
    } catch {
      // ignore malformed session payload
    }
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable);
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
        return;
      }
      if (event.key === "Escape") {
        setPaletteOpen(false);
        return;
      }
      if (!typing && event.key === "\\" && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        setTrayOpen((open) => !open);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="h-screen flex overflow-hidden">
      <Sidebar mobileOpen={mobileOpen} onMobileOpenChange={setMobileOpen} />
      <div className="flex-1 min-w-0 flex flex-col">
        <TopStatusBar
          unread={unread}
          onToggleTray={() => setTrayOpen((open) => !open)}
          onOpenNav={() => setMobileOpen(true)}
        />
        <main className="flex-1 min-h-0 overflow-hidden">{children}</main>
      </div>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      <IncidentTray open={trayOpen} onOpenChange={setTrayOpen} onUnread={setUnread} />
      <Toaster theme="dark" position="bottom-right" />
    </div>
  );
}
