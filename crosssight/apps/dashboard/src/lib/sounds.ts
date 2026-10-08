import { getPrefs } from "@/lib/prefs";

export type CueKind = "alert-critical" | "alert-high" | "plate-tick";

let audioCtx: AudioContext | null = null;
let interacted = false;
let lastTick = 0;

export function unlockAudio(): void {
  if (typeof window === "undefined") return;
  interacted = true;
  const Ctx = window.AudioContext;
  if (!audioCtx) audioCtx = new Ctx();
  if (audioCtx.state === "suspended") void audioCtx.resume();
}

function allowed(kind: CueKind): boolean {
  const audio = getPrefs().audio;
  if (audio === "off") return false;
  if (audio === "alerts-only") return kind !== "plate-tick";
  return true;
}

export function playCue(kind: CueKind): void {
  if (!interacted || !audioCtx || !allowed(kind)) return;
  if (kind === "plate-tick") {
    const nowMs = performance.now();
    if (nowMs - lastTick < 300) return;
    lastTick = nowMs;
  }

  const ctx = audioCtx;
  const now = ctx.currentTime;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.connect(gain);
  gain.connect(ctx.destination);

  if (kind === "alert-critical") {
    osc.type = "sawtooth";
    osc.frequency.setValueAtTime(880, now);
    osc.frequency.linearRampToValueAtTime(660, now + 0.2);
    gain.gain.setValueAtTime(0.08, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.2);
    osc.start(now);
    osc.stop(now + 0.2);
    return;
  }

  if (kind === "alert-high") {
    osc.type = "sine";
    osc.frequency.setValueAtTime(660, now);
    gain.gain.setValueAtTime(0.08, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.12);
    osc.start(now);
    osc.stop(now + 0.12);
    return;
  }

  osc.type = "sine";
  osc.frequency.setValueAtTime(1200, now);
  gain.gain.setValueAtTime(0.05, now);
  gain.gain.exponentialRampToValueAtTime(0.001, now + 0.04);
  osc.start(now);
  osc.stop(now + 0.04);
}
