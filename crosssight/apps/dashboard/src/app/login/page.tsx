"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

export default function LoginPage() {
  const router = useRouter();
  const params = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<"sim" | "video" | null>(null);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const submitter = (e.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    const mode = submitter?.value === "video" ? "video" : "sim";
    setLoading(mode);
    setError(null);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password, mode }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.error ?? "Login failed");
      }
      if (body.last_login_at) {
        sessionStorage.setItem(
          "crosssight:last-login",
          JSON.stringify({ last_login_at: body.last_login_at, last_login_ip: body.last_login_ip ?? null }),
        );
      }
      const dest = body.mode === "video" ? "/wall" : (params.get("from") ?? "/live");
      router.push(dest);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-md rounded-lg border border-border bg-surface-raised p-6 shadow-xl">
        <div className="mb-6 text-center">
          <p className="font-mono text-2xl text-accent tracking-tight">ANPR</p>
          <p className="text-sm text-muted mt-1">Control room sign-in</p>
        </div>

        <form onSubmit={onSubmit} className="space-y-4">
          <Input
            label="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
          />
          <Input
            label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
          {error && <p className="text-danger text-sm">{error}</p>}
          <div className="space-y-2 pt-1">
            <Button type="submit" value="sim" className="w-full flex-col gap-0.5 py-3 h-auto" disabled={loading !== null}>
              <span>{loading === "sim" ? "Signing in…" : "Simulated city"}</span>
              <span className="text-[11px] font-normal opacity-80">Synthetic cameras, traffic, and scripted alerts</span>
            </Button>
            <Button
              type="submit"
              value="video"
              variant="secondary"
              className="w-full flex-col gap-0.5 py-3 h-auto"
              disabled={loading !== null}
            >
              <span>{loading === "video" ? "Starting cameras…" : "Camera videos"}</span>
              <span className="text-[11px] font-normal text-muted">
                Traffic footage as live cameras. Plates are read from the video, with no simulated history.
              </span>
            </Button>
          </div>
        </form>

        {process.env.NEXT_PUBLIC_APP_ENV === "dev" && (
          <details className="mt-4 text-[10px] text-muted text-center">
            <summary className="cursor-pointer">Demo credentials</summary>
            admin / admin123 · operator / operator123 · analyst / analyst123
          </details>
        )}
      </div>
    </div>
  );
}
