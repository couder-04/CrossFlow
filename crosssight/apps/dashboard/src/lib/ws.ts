import type { WsMessage } from "@/types";
import { wsUrlFromApi } from "@/lib/wsUrl";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/live";

export type WsHandler = (message: WsMessage) => void;
export type SocketState = "live" | "reconnecting" | "offline";
type StateHandler = (state: SocketState) => void;

/** Ask the server which websocket to use, so a rebuilt page cannot keep dialing a dead tunnel. */
export async function resolveLiveWsUrl(): Promise<string> {
  if (typeof window === "undefined") {
    const api = process.env.API_INTERNAL_URL ?? process.env.NEXT_PUBLIC_API_URL;
    return api ? wsUrlFromApi(api) : WS_URL;
  }
  try {
    const res = await fetch("/api/live-config", { cache: "no-store" });
    if (!res.ok) return WS_URL;
    const body = (await res.json()) as { wsUrl?: string };
    return body.wsUrl || WS_URL;
  } catch {
    return WS_URL;
  }
}

export class LiveSocket {
  private ws: WebSocket | null = null;
  private handlers = new Set<WsHandler>();
  private stateHandlers = new Set<StateHandler>();
  private subscribed = new Set<string>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;
  private failures = 0;
  private generation = 0;
  /** Set only for a socket that is not the shared live feed (the camera wall). */
  private readonly explicitUrl: string | null;

  constructor(url?: string) {
    this.explicitUrl = url ?? null;
  }

  connect() {
    if (typeof window === "undefined" || this.closed) return;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const generation = ++this.generation;
    void this.open(generation);
  }

  private async open(generation: number) {
    const url = this.explicitUrl ?? (await resolveLiveWsUrl());
    if (this.closed || generation !== this.generation) return;
    this.dropSocket();
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => {
      if (this.closed || this.ws !== ws) return;
      this.failures = 0;
      this.emitState("live");
      if (this.subscribed.size > 0) {
        this.send({ subscribe: Array.from(this.subscribed) });
      }
    };

    ws.onmessage = (event) => {
      if (this.ws !== ws) return;
      try {
        const msg = JSON.parse(event.data) as WsMessage;
        this.handlers.forEach((handler) => {
          try {
            handler(msg);
          } catch {
            // one bad frame must not kill the feed
          }
        });
      } catch {
        // ignore malformed frames
      }
    };

    ws.onclose = () => {
      if (this.closed || this.ws !== ws) return;
      this.ws = null;
      this.failures += 1;
      this.emitState(this.failures >= 3 ? "offline" : "reconnecting");
      this.reconnectTimer = setTimeout(() => {
        if (!this.closed) this.connect();
      }, 3000);
    };
  }

  onState(handler: StateHandler) {
    this.stateHandlers.add(handler);
    return () => this.stateHandlers.delete(handler);
  }

  private emitState(state: SocketState) {
    this.stateHandlers.forEach((handler) => handler(state));
  }

  subscribe(channels: string[]) {
    channels.forEach((channel) => this.subscribed.add(channel));
    this.send({ subscribe: channels });
  }

  unsubscribe(channels: string[]) {
    channels.forEach((channel) => this.subscribed.delete(channel));
    this.send({ unsubscribe: channels });
  }

  onMessage(handler: WsHandler) {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  private send(payload: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  private dropSocket() {
    const ws = this.ws;
    this.ws = null;
    if (!ws) return;
    ws.onopen = null;
    ws.onmessage = null;
    ws.onclose = null;
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
    }
  }

  close() {
    this.closed = true;
    this.generation += 1;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.dropSocket();
  }
}

export async function openWallSocket(): Promise<LiveSocket> {
  const tokenRes = await fetch("/api/auth/ws-token");
  if (!tokenRes.ok) {
    throw new Error("Not authenticated");
  }
  const body = (await tokenRes.json()) as { token: string };
  const live = await resolveLiveWsUrl();
  const base = live.replace(/\/ws\/live\/?$/, "");
  return new LiveSocket(`${base}/ws/wall?token=${encodeURIComponent(body.token)}`);
}
