import type { Role, UserSession } from "@/types";

export const AUTH_COOKIE = "anpr_token";
export const USER_COOKIE = "anpr_user";

/** Session cookies: HttpOnly always, Secure only when the dashboard is served over HTTPS. */
export function sessionCookieOptions(maxAge: number) {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
    maxAge,
  };
}

export function canAccessTrack(role: Role): boolean {
  return role === "admin" || role === "operator";
}

export function canAccessAdmin(role: Role): boolean {
  return role === "admin";
}

export type NavIconName =
  | "Radio"
  | "Grid3x3"
  | "Route"
  | "Waypoints"
  | "Activity"
  | "Bell"
  | "HeartPulse"
  | "ClipboardCheck"
  | "Search"
  | "Upload"
  | "Download"
  | "Settings"
  | "TrafficCone";

export interface NavItem {
  href: string;
  label: string;
  icon: NavIconName;
  roles: Role[];
}

export const NAV_GROUPS: { id: string; label: string; hrefs: string[] }[] = [
  { id: "monitor", label: "Monitor", hrefs: ["/live", "/wall", "/alerts", "/health"] },
  { id: "investigate", label: "Investigate", hrefs: ["/track", "/investigate", "/review"] },
  { id: "analyze", label: "Analyze", hrefs: ["/flow", "/analytics", "/signals"] },
  { id: "manage", label: "Manage", hrefs: ["/imports", "/exports", "/admin"] },
];

export function navItemsForRole(role: Role): NavItem[] {
  const items: NavItem[] = [
    { href: "/live", label: "Live", icon: "Radio", roles: ["admin", "operator", "analyst"] },
    { href: "/wall", label: "Wall", icon: "Grid3x3", roles: ["admin", "operator"] },
    { href: "/track", label: "Track", icon: "Route", roles: ["admin", "operator"] },
    { href: "/flow", label: "Flow", icon: "Waypoints", roles: ["admin", "operator", "analyst"] },
    { href: "/analytics", label: "Analytics", icon: "Activity", roles: ["admin", "operator", "analyst"] },
    { href: "/signals", label: "Signals", icon: "TrafficCone", roles: ["admin", "operator", "analyst"] },
    { href: "/alerts", label: "Alerts", icon: "Bell", roles: ["admin", "operator", "analyst"] },
    { href: "/health", label: "Health", icon: "HeartPulse", roles: ["admin", "operator", "analyst"] },
    { href: "/review", label: "Review", icon: "ClipboardCheck", roles: ["admin", "operator"] },
    { href: "/investigate", label: "Investigate", icon: "Search", roles: ["admin", "operator"] },
    { href: "/imports", label: "Import", icon: "Upload", roles: ["admin", "operator"] },
    { href: "/exports", label: "Export", icon: "Download", roles: ["admin", "operator", "analyst"] },
    { href: "/admin", label: "Admin", icon: "Settings", roles: ["admin"] },
  ];
  return items.filter((item) => item.roles.includes(role));
}

export function parseUserCookie(raw: string | undefined): UserSession | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as UserSession;
    if (!parsed.username || !parsed.role) return null;
    return parsed;
  } catch {
    return null;
  }
}
