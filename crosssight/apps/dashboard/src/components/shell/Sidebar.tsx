"use client";

import { LogOut, PanelLeft } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { navIcons } from "@/components/ui/icons";
import { NAV_GROUPS, navItemsForRole } from "@/lib/auth";
import { sourceMode, type SourceMode } from "@/lib/source";
import { defaultPrefs, getPrefs, subscribePrefs, updatePrefs, type AudioPref, type Density, type Prefs } from "@/lib/prefs";
import { cn } from "@/lib/utils";
import type { NavItem } from "@/lib/auth";
import type { Role, UserSession } from "@/types";

export function Sidebar({
  mobileOpen,
  onMobileOpenChange,
}: {
  mobileOpen: boolean;
  onMobileOpenChange: (open: boolean) => void;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<UserSession | null>(null);
  const [mode, setMode] = useState<SourceMode>("sim");
  const [prefs, setPrefs] = useState<Prefs>(defaultPrefs);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    fetch("/api/auth/me")
      .then((response) => (response.ok ? response.json() : null))
      .then(setUser)
      .catch(() => setUser(null));
  }, []);

  useEffect(() => {
    setMode(sourceMode());
    setPrefs(getPrefs());
    return subscribePrefs(setPrefs);
  }, []);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (mobileOpen && !dialog.open) dialog.showModal();
    if (!mobileOpen && dialog.open) dialog.close();
  }, [mobileOpen]);

  const items = user ? navItemsForRole(user.role as Role) : [];
  const collapsed = prefs.sidebarCollapsed;

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }

  return (
    <>
      <aside
        className={cn(
          "no-print hidden md:flex h-full shrink-0 flex-col border-r border-border bg-surface-raised transition-[width] duration-200",
          collapsed ? "w-14" : "w-[200px]",
        )}
      >
        <div className={cn("flex items-center gap-2 px-3 h-12 border-b border-border", collapsed && "justify-center px-0")}>
          {!collapsed && (
            <Link href="/live" className="font-mono text-accent font-semibold tracking-tight">
              ANPR
            </Link>
          )}
          <button
            type="button"
            className={cn(
              "text-muted hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded",
              !collapsed && "ml-auto",
            )}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={() => updatePrefs({ sidebarCollapsed: !collapsed })}
          >
            <PanelLeft size={14} />
          </button>
        </div>
        <nav className="flex-1 overflow-y-auto py-3" aria-label="Primary">
          <NavGroups items={items} pathname={pathname} collapsed={collapsed} />
        </nav>
        <UserMenu user={user} prefs={prefs} collapsed={collapsed} onLogout={logout} />
        <p className={cn("px-3 py-2 text-[10px] text-muted border-t border-border", collapsed && "text-center px-0")}>
          {collapsed ? "v" : mode === "video" ? "Camera videos" : "Simulated city"}
        </p>
      </aside>

      <dialog
        ref={dialogRef}
        className="md:hidden fixed inset-y-0 left-0 m-0 p-0 h-full max-h-none w-72 max-w-[85vw] bg-surface-raised text-slate-100 border-0 border-r border-border"
        onClose={() => onMobileOpenChange(false)}
        aria-label="Navigation"
      >
        <div className="h-full flex flex-col">
          <div className="flex items-center justify-between px-3 h-12 border-b border-border">
            <span className="font-mono text-accent font-semibold">ANPR</span>
            <Button variant="ghost" size="sm" aria-label="Close navigation" onClick={() => onMobileOpenChange(false)}>
              Close
            </Button>
          </div>
          <nav className="flex-1 overflow-y-auto py-3" onClick={() => onMobileOpenChange(false)}>
            <NavGroups items={items} pathname={pathname} collapsed={false} />
          </nav>
          <UserMenu user={user} prefs={prefs} collapsed={false} onLogout={logout} />
        </div>
      </dialog>
    </>
  );
}

function NavGroups({
  items,
  pathname,
  collapsed,
}: {
  items: NavItem[];
  pathname: string;
  collapsed: boolean;
}) {
  return (
    <>
      {NAV_GROUPS.map((group) => {
        const groupItems = group.hrefs
          .map((href) => items.find((item) => item.href === href))
          .filter((item): item is NavItem => Boolean(item));
        if (groupItems.length === 0) return null;
        return (
          <div key={group.id} className="mb-3">
            {!collapsed && <p className="text-label px-3 mb-1">{group.label}</p>}
            {groupItems.map((item) => {
              const Icon = navIcons[item.icon];
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-label={item.label}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex items-center mx-2 rounded px-2 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                    collapsed && "justify-center px-0",
                    active
                      ? "bg-surface-overlay text-white border-l-2 border-accent"
                      : "text-muted hover:text-slate-100 hover:bg-surface-overlay/60 border-l-2 border-transparent",
                  )}
                >
                  <Icon size={14} className={collapsed ? "" : "mr-1.5"} aria-hidden />
                  {!collapsed && item.label}
                </Link>
              );
            })}
          </div>
        );
      })}
    </>
  );
}

function UserMenu({
  user,
  prefs,
  collapsed,
  onLogout,
}: {
  user: UserSession | null;
  prefs: Prefs;
  collapsed: boolean;
  onLogout: () => void;
}) {
  return (
    <div className="border-t border-border p-2 space-y-2">
      {user && !collapsed && (
        <p className="text-xs text-muted font-mono px-1">
          {user.username}
          <span className="text-accent ml-2 uppercase">{user.role}</span>
        </p>
      )}
      {!collapsed && (
        <>
          <label className="block text-label px-1">
            Density
            <select
              className="mt-1 w-full"
              value={prefs.density}
              aria-label="Density"
              onChange={(event) => updatePrefs({ density: event.target.value as Density })}
            >
              <option value="comfortable">Comfortable</option>
              <option value="compact">Compact</option>
            </select>
          </label>
          <label className="block text-label px-1">
            Audio
            <select
              className="mt-1 w-full"
              value={prefs.audio}
              aria-label="Audio cues"
              onChange={(event) => updatePrefs({ audio: event.target.value as AudioPref })}
            >
              <option value="off">Off</option>
              <option value="alerts-only">Alerts only</option>
              <option value="all">All</option>
            </select>
          </label>
        </>
      )}
      <Button variant="ghost" size="sm" className="w-full" onClick={onLogout} aria-label="Logout">
        <LogOut size={14} className={collapsed ? "" : "mr-1.5"} aria-hidden />
        {!collapsed && "Logout"}
      </Button>
    </div>
  );
}
