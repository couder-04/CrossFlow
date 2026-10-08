import { clsx, type ClassValue } from "clsx";
import { splitLongToH3Index } from "h3-js";

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

export function formatTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function clockParts(date: Date, timeZone: string, withSeconds = false): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    second: withSeconds ? "2-digit" : undefined,
    hourCycle: "h23",
  }).format(date);
}

export function formatTsWithZone(iso: string): { primary: string; secondary: string } {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return { primary: iso, secondary: "" };
  return {
    primary: `${clockParts(date, "Asia/Kolkata")} IST`,
    secondary: `${clockParts(date, "UTC")} UTC`,
  };
}

export function formatDualClock(date: Date): { ist: string; utc: string } {
  return { ist: clockParts(date, "Asia/Kolkata"), utc: clockParts(date, "UTC") };
}

export function formatIstTime(date: Date, withSeconds = false): string {
  return clockParts(date, "Asia/Kolkata", withSeconds);
}

export function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const sec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.round(hr / 24)}d ago`;
}

export function startOfTodayIst(): Date {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const year = parts.find((part) => part.type === "year")?.value ?? "1970";
  const month = parts.find((part) => part.type === "month")?.value ?? "01";
  const day = parts.find((part) => part.type === "day")?.value ?? "01";
  return new Date(`${year}-${month}-${day}T00:00:00+05:30`);
}

export function titleCase(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

const SEVERITY_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };

export function severityRank(severity: string): number {
  return SEVERITY_RANK[severity] ?? 9;
}

export function formatConfidence(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export const MAP_STYLE =
  process.env.NEXT_PUBLIC_MAP_STYLE_URL ??
  "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

export const DEFAULT_CENTER: [number, number] = [73.8567, 18.5204];

/** Convert Python h3 uint64 int to H3 index string (h3-js v4). */
export function h3IntToString(cell: string | number): string {
  if (typeof cell === "string" && cell.length > 8) return cell;
  const value = typeof cell === "number" ? cell : Number(cell);
  const lower = value >>> 0;
  const upper = Math.floor(value / 4294967296) >>> 0;
  return splitLongToH3Index(lower, upper);
}

export function congestionColor(index: number): [number, number, number, number] {
  if (index >= 0.7) return [239, 68, 68, 220];
  if (index >= 0.5) return [249, 115, 22, 200];
  if (index >= 0.3) return [234, 179, 8, 180];
  return [34, 197, 94, 160];
}

export function cameraStatusColor(status: string): [number, number, number, number] {
  switch (status) {
    case "active":
    case "healthy":
      return [34, 197, 94, 220];
    case "degraded":
      return [234, 179, 8, 220];
    case "offline":
      return [148, 163, 184, 200];
    case "stale":
      return [100, 116, 139, 200];
    default:
      return [96, 165, 250, 220];
  }
}

/** Ring color for the live map. Status is drawn as a stroke, separate from volume fill. */
export function cameraRingColor(status: string): [number, number, number, number] {
  switch (status) {
    case "healthy":
    case "active":
      return [34, 197, 94, 255];
    case "degraded":
      return [234, 179, 8, 255];
    case "stale":
      return [148, 163, 184, 255];
    case "offline":
      return [239, 68, 68, 255];
    default:
      return cameraStatusColor(status);
  }
}

export function volumeFillColor(volume: number, maxVolume: number): [number, number, number, number] {
  const t = maxVolume > 0 ? volume / maxVolume : 0;
  if (t >= 0.66) return [239, 68, 68, 220];
  if (t >= 0.33) return [245, 158, 11, 210];
  return [34, 197, 94, 200];
}
