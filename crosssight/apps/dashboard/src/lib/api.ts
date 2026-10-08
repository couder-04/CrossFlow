import { sourceMode } from "@/lib/source";
import { publicApiError, upstreamFetch } from "@/lib/upstream";
import type {
  Alert,
  AuditEntry,
  Bottleneck,
  Camera,
  FlowWindow,
  GeoJSONFeatureCollection,
  HeatmapResponse,
  LatestRunResponse,
  LoginResponse,
  MeasuredDemand,
  ODCell,
  SegmentCongestion,
  SignalSummary,
  UserSession,
  VolumeAnomaly,
  WatchlistEntry,
  Zone,
} from "@/types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function isServer() {
  return typeof window === "undefined";
}

function errorMessage(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return fallback;
  const record = body as { detail?: unknown; error?: unknown };
  const value = record.detail ?? record.error;
  if (typeof value === "string" && value) return publicApiError(value);
  if (Array.isArray(value)) {
    const parts = value.map((item) => {
      if (item && typeof item === "object" && "msg" in item) return String(item.msg);
      return String(item);
    });
    return parts.filter(Boolean).join("; ") || fallback;
  }
  if (value && typeof value === "object" && "message" in value) {
    return String((value as { message: unknown }).message);
  }
  return publicApiError(fallback);
}

const RETRYABLE_STATUS = new Set([502, 503, 504]);

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function apiBase(): string {
  if (isServer()) {
    return process.env.API_INTERNAL_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  }
  return "/api/backend";
}

async function request<T>(
  path: string,
  options: RequestInit & { token?: string } = {},
): Promise<T> {
  const { token, ...init } = options;
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const method = (init.method ?? "GET").toUpperCase();
  const canRetry = method === "GET" || method === "HEAD";
  const attempts = canRetry ? 2 : 1;

  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      const res = await fetch(`${apiBase()}${path}`, { ...init, headers, cache: "no-store" });
      if (!res.ok) {
        let detail = res.statusText;
        try {
          detail = errorMessage(await res.json(), detail);
        } catch {
          // ignore
        }
        if (canRetry && attempt + 1 < attempts && RETRYABLE_STATUS.has(res.status)) {
          await delay(400);
          continue;
        }
        throw new ApiError(res.status, publicApiError(detail));
      }
      if (res.status === 204) return undefined as T;
      return (await res.json()) as T;
    } catch (err) {
      if (err instanceof ApiError) throw err;
      if (attempt + 1 < attempts) {
        await delay(400);
        continue;
      }
      throw new ApiError(502, "Control room API is unreachable");
    }
  }
  throw new ApiError(502, "Control room API is unreachable");
}

export const api = {
  login(username: string, password: string): Promise<LoginResponse> {
    const base = process.env.API_INTERNAL_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    return upstreamFetch(`${base}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    }).then(async (res) => {
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail = typeof body.detail === "string" ? body.detail : "Login failed";
        throw new ApiError(res.status, publicApiError(detail));
      }
      return res.json() as Promise<LoginResponse>;
    });
  },

  cameras(token?: string) {
    return request<Camera[]>(`/cameras?source=${sourceMode()}`, { token });
  },

  cameraFrame(id: string, cacheBust?: number) {
    const q = cacheBust != null ? `?t=${cacheBust}` : "";
    return request<{ url: string; key: string }>(
      `/cameras/${encodeURIComponent(id)}/frame${q}`,
    );
  },

  createCamera(data: Partial<Camera> & { id: string; name: string; lat: number; lng: number }, token?: string) {
    return request<Camera>("/cameras", { method: "POST", body: JSON.stringify(data), token });
  },

  deleteCamera(id: string, token?: string) {
    return request<void>(`/cameras/${id}`, { method: "DELETE", token });
  },

  zones(token?: string) {
    return request<Zone[]>("/zones", { token });
  },

  watchlist(token?: string) {
    return request<WatchlistEntry[]>("/watchlist", { token });
  },

  addWatchlist(
    body: { plate_norm: string; reason: string; severity?: string },
    token?: string,
  ) {
    return request<WatchlistEntry>("/watchlist", {
      method: "POST",
      body: JSON.stringify(body),
      token,
    });
  },

  deleteWatchlist(plate: string, token?: string) {
    return request<void>(`/watchlist/${encodeURIComponent(plate)}`, { method: "DELETE", token });
  },

  importWatchlistCsv(file: File, token?: string) {
    const form = new FormData();
    form.append("file", file);
    return request<{ imported: number }>("/watchlist/import", {
      method: "POST",
      body: form,
      token,
    });
  },

  audit(token?: string, limit = 100) {
    return request<AuditEntry[]>(`/audit?limit=${limit}`, { token });
  },

  alerts(params: Record<string, string> = {}, token?: string) {
    const qs = new URLSearchParams({ ...params, source: sourceMode() }).toString();
    return request<Alert[]>(`/alerts?${qs}`, { token });
  },

  alert(id: string, token?: string) {
    return request<Alert>(`/alerts/${id}`, { token });
  },

  ackAlert(id: string, token?: string) {
    return request<Alert>(`/alerts/${id}/ack`, { method: "POST", body: "{}", token });
  },

  dispatchAlert(id: string, dispatched_to: string, token?: string) {
    return request<Alert>(`/alerts/${id}/dispatch`, {
      method: "POST",
      body: JSON.stringify({ dispatched_to }),
      token,
    });
  },

  closeAlert(id: string, note: string, token?: string) {
    return request<Alert>(`/alerts/${id}/close`, {
      method: "POST",
      body: JSON.stringify({ note }),
      token,
    });
  },

  trajectory(params: {
    plate: string;
    case_id: string;
    from?: string;
    to?: string;
    fuzzy?: boolean;
  }, token?: string) {
    const qs = new URLSearchParams();
    qs.set("plate", params.plate);
    qs.set("case_id", params.case_id);
    if (params.from) qs.set("from", params.from);
    if (params.to) qs.set("to", params.to);
    if (params.fuzzy) qs.set("fuzzy", "true");
    return request<GeoJSONFeatureCollection>(`/trajectory?${qs}`, { token });
  },

  heatmap(window = "15m", token?: string, at?: string) {
    const qs = new URLSearchParams({ window, source: sourceMode() });
    if (at) qs.set("at", at);
    return request<HeatmapResponse>(`/analytics/heatmap?${qs}`, { token });
  },

  flow(camera_id: string, from: string, to: string, token?: string) {
    const qs = new URLSearchParams({ camera_id, from, to });
    return request<{ windows: FlowWindow[] }>(`/analytics/flow?${qs}`, { token });
  },

  segments(at: string, token?: string) {
    return request<{ segments: SegmentCongestion[] }>(
      `/analytics/segments?at=${encodeURIComponent(at)}`,
      { token },
    );
  },

  od(hour: number, date: string, token?: string) {
    const qs = new URLSearchParams({ hour: String(hour), date });
    return request<{ cells: ODCell[] }>(`/analytics/od?${qs}`, { token });
  },

  bottlenecks(token?: string) {
    return request<{ bottlenecks: Bottleneck[] }>("/analytics/bottlenecks", { token });
  },

  anomalies(token?: string) {
    return request<{ anomalies: VolumeAnomaly[] }>("/analytics/anomalies", { token });
  },

  routeDensity(window = "1h", token?: string) {
    return request<{
      corridors: Array<{ camera_a: string; camera_b: string; hop_count: number }>;
      window: string;
    }>(`/analytics/route-density?window=${encodeURIComponent(window)}`, { token });
  },

  signalsSummary(token?: string) {
    return request<SignalSummary>("/signals/summary", { token });
  },

  signalsLatestRun(token?: string) {
    return request<LatestRunResponse>("/signals/runs/latest", { token });
  },

  signalsDemand(cameras: string, from: string, to: string, token?: string) {
    const qs = new URLSearchParams({ cameras, from, to });
    return request<MeasuredDemand>(`/signals/demand?${qs}`, { token });
  },

  cropUrl(key: string, token?: string) {
    return request<{ key: string; url: string }>(
      `/crops?key=${encodeURIComponent(key)}`,
      { token },
    );
  },

  overview(token?: string) {
    return request<Record<string, unknown>>("/ops/overview", { token });
  },

  cameraHealth(token?: string) {
    return request<{ summary: Record<string, number>; cameras: Array<Record<string, unknown>> }>(
      "/ops/cameras/health",
      { token },
    );
  },

  scanHealth(token?: string) {
    return request<{ created: number }>("/ops/cameras/health/scan", { method: "POST", body: "{}", token });
  },

  cameraOd(token?: string) {
    return request<{ cells: Array<Record<string, unknown>> }>("/ops/od", { token });
  },

  travel(token?: string) {
    return request<{ routes: Array<Record<string, unknown>> }>("/ops/travel", { token });
  },

  dwell(token?: string) {
    return request<{ sessions: Array<Record<string, unknown>> }>("/ops/dwell", { token });
  },

  vehicles(token?: string) {
    return request<Record<string, unknown>>("/ops/vehicles", { token });
  },

  recentReads(cameraId?: string, token?: string) {
    const qs = new URLSearchParams({ source: sourceMode() });
    if (cameraId) qs.set("camera_id", cameraId);
    return request<{ reads: Array<Record<string, unknown>> }>(`/ops/reads?${qs}`, { token });
  },

  investigation(plate: string, token?: string) {
    return request<Record<string, unknown>>(`/ops/investigation?plate=${encodeURIComponent(plate)}`, { token });
  },

  reviewAlert(id: string, status: string, note: string, token?: string) {
    return request<Alert>(`/alerts/${id}/review`, {
      method: "POST",
      body: JSON.stringify({ status, note }),
      token,
    });
  },

  reviews(id: string, token?: string) {
    return request<Array<Record<string, unknown>>>(`/alerts/${id}/reviews`, { token });
  },

  imports(token?: string) {
    return request<Array<Record<string, unknown>>>("/imports", { token });
  },

  previewImport(kind: string, file: File, token?: string) {
    const form = new FormData();
    form.append("kind", kind);
    form.append("file", file);
    return request<Record<string, unknown>>("/imports/preview", { method: "POST", body: form, token });
  },

  confirmImport(id: string, acknowledgeInvalid: boolean, overwrite: boolean, token?: string) {
    const qs = new URLSearchParams({
      acknowledge_invalid: String(acknowledgeInvalid),
      overwrite: String(overwrite),
    });
    return request<Record<string, unknown>>(`/imports/${id}/confirm?${qs}`, { method: "POST", body: "{}", token });
  },

  uploadMedia(kind: string, cameraId: string, file: File, capturedAt: string, token?: string) {
    const form = new FormData();
    form.append("kind", kind);
    form.append("camera_id", cameraId);
    form.append("file", file);
    if (capturedAt) form.append("captured_at", capturedAt);
    return request<Record<string, unknown>>("/uploads/media", { method: "POST", body: form, token });
  },

  exports(token?: string) {
    return request<Array<Record<string, unknown>>>("/exports", { token });
  },

  createExport(kind: string, format: string, filters: Record<string, string>, token?: string) {
    const form = new FormData();
    form.append("kind", kind);
    form.append("fmt", format);
    form.append("filters", JSON.stringify(filters));
    return request<Record<string, unknown>>("/exports", { method: "POST", body: form, token });
  },

  retryExport(id: string, token?: string) {
    return request<Record<string, unknown>>(`/exports/${id}/retry`, { method: "POST", body: "{}", token });
  },

  retryUpload(id: string, token?: string) {
    return request<Record<string, unknown>>(`/uploads/${id}/retry`, { method: "POST", body: "{}", token });
  },
};

export type { UserSession };
