import type { Role } from "@/types";

export const PREFS_KEY = "crosssight:prefs";

export type AudioPref = "off" | "alerts-only" | "all";
export type Density = "comfortable" | "compact";

export interface LiveSidebarPrefs {
  reads: boolean;
  alerts: boolean;
}

export interface Prefs {
  sidebarCollapsed: boolean;
  autoOpenTray?: boolean;
  audio: AudioPref;
  density: Density;
  liveSidebar: LiveSidebarPrefs;
}

export const defaultPrefs: Prefs = {
  sidebarCollapsed: false,
  audio: "alerts-only",
  density: "comfortable",
  liveSidebar: { reads: false, alerts: false },
};

type Listener = (prefs: Prefs) => void;
const listeners = new Set<Listener>();

function merge(raw: Partial<Prefs> | null): Prefs {
  return {
    ...defaultPrefs,
    ...raw,
    liveSidebar: { ...defaultPrefs.liveSidebar, ...(raw?.liveSidebar ?? {}) },
  };
}

export function getPrefs(): Prefs {
  if (typeof window === "undefined") return defaultPrefs;
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return defaultPrefs;
    return merge(JSON.parse(raw) as Partial<Prefs>);
  } catch {
    return defaultPrefs;
  }
}

export function updatePrefs(patch: Partial<Prefs>): Prefs {
  const current = getPrefs();
  const next = merge({
    ...current,
    ...patch,
    liveSidebar: { ...current.liveSidebar, ...(patch.liveSidebar ?? {}) },
  });
  localStorage.setItem(PREFS_KEY, JSON.stringify(next));
  document.documentElement.dataset.density = next.density;
  listeners.forEach((listener) => listener(next));
  return next;
}

export function subscribePrefs(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function autoOpenTrayFor(role: Role | null, prefs: Prefs): boolean {
  if (typeof prefs.autoOpenTray === "boolean") return prefs.autoOpenTray;
  return role === "admin" || role === "operator";
}
