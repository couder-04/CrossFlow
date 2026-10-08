"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorState } from "@/components/common/ErrorState";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Kpi } from "@/components/ui/Kpi";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { api } from "@/lib/api";
import type {
  LatestRunResponse,
  MeasuredDemand,
  PipelineReport,
  SignalSummary,
} from "@/types";

const ARMS = ["N", "S", "E", "W"] as const;

function pct(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

/** A gain only counts when its whole interval is on one side of zero. */
function interval(lo: number | null, hi: number | null): { text: string; tone: "success" | "danger" | "warning" } {
  if (lo === null || hi === null) return { text: "no interval", tone: "warning" };
  const text = `${lo.toFixed(2)} to ${hi.toFixed(2)}`;
  if (lo > 0) return { text, tone: "success" };
  if (hi < 0) return { text, tone: "danger" };
  return { text, tone: "warning" };
}

function HeadlinesCard({ summary }: { summary: SignalSummary }) {
  return (
    <Card>
      <CardHeader title="Published evaluation: fuel vs the best baseline" />
      <div className="p-[var(--card-p)] space-y-3">
        <table className="w-full text-sm">
          <thead className="text-label text-left">
            <tr>
              <th className="py-1">Scenario</th>
              <th>Best baseline</th>
              <th>Fuel reduction</th>
              <th>95% CI</th>
              <th>Established?</th>
            </tr>
          </thead>
          <tbody>
            {summary.headlines.map((h) => {
              const ci = interval(h.ci_lo, h.ci_hi);
              return (
                <tr key={h.scenario} className="border-t border-border">
                  <td className="py-1.5 font-mono">{h.scenario}</td>
                  <td>{h.best_baseline}</td>
                  <td className="font-mono">{pct(h.pct_fuel_reduction_vs_best)}</td>
                  <td className="font-mono">{ci.text}</td>
                  <td>
                    <Badge tone={ci.tone} case="normal">
                      {ci.tone === "success" ? "yes" : ci.tone === "danger" ? "worse" : "not shown"}
                    </Badge>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {summary.note && <p className="text-xs text-muted">{summary.note}</p>}
        {summary.label && <p className="text-xs text-muted">{summary.label}.</p>}
      </div>
    </Card>
  );
}

function RunCard({ report }: { report: PipelineReport }) {
  const d = report.demand;
  const c = report.comparison;
  return (
    <Card>
      <CardHeader title="Latest pipeline run: city cameras to signal control" />
      <div className="p-[var(--card-p)] space-y-4">
        <p className="text-xs text-muted">
          {report.source.kind}; camera {report.source.camera} at {String(report.source.hour).padStart(2, "0")}:00;{" "}
          {report.run.mode} mode, seeds {report.run.seeds.join(", ")}; {d.vehicle_mix_source}.
        </p>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {ARMS.map((a) => (
            <Kpi
              key={a}
              label={`Arm ${a} veh/h (GEH ${d.geh_observed_vs_simulated[a] ?? "n/a"})`}
              value={String(d.observed_veh_h[a] ?? "n/a")}
              tone={(d.geh_observed_vs_simulated[a] ?? 0) >= d.geh_target ? "warning" : undefined}
            />
          ))}
        </div>
        <table className="w-full text-sm">
          <thead className="text-label text-left">
            <tr>
              <th className="py-1">Controller</th>
              <th>Fuel / veh (L)</th>
              <th>Wait (s)</th>
              <th>Queue</th>
              <th>vs XtraFlow</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(report.per_controller).map(([name, m]) => {
              const v = c.vs_baseline?.[name];
              return (
                <tr key={name} className="border-t border-border">
                  <td className="py-1.5 font-mono">
                    {name}
                    {name === c.best_baseline && (
                      <Badge tone="accent" case="normal" className="ml-2">best baseline</Badge>
                    )}
                  </td>
                  <td className="font-mono">{m.fuel_per_vehicle_L.toFixed(4)}</td>
                  <td className="font-mono">{m.mean_waiting_s.toFixed(1)}</td>
                  <td className="font-mono">{m.mean_queue_veh.toFixed(1)}</td>
                  <td className="font-mono">
                    {v ? `${pct(v.pct_fuel_reduction)} (${interval(v.ci_lo, v.ci_hi).text})` : "-"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="text-sm text-slate-100">{c.verdict}</p>
        <ul className="list-disc pl-4 text-xs text-muted space-y-1">
          {report.caveats.map((x) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      </div>
    </Card>
  );
}

function LiveDemandCard() {
  const [cameras, setCameras] = useState("");
  const [hours, setHours] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<MeasuredDemand | null>(null);

  const measure = async () => {
    setBusy(true);
    setError(null);
    try {
      const to = new Date();
      const from = new Date(to.getTime() - hours * 3600_000);
      setResult(await api.signalsDemand(cameras.trim(), from.toISOString(), to.toISOString()));
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader title="Measure junction demand from live cameras" />
      <div className="p-[var(--card-p)] space-y-3">
        <p className="text-xs text-muted">
          Name the camera watching each arm, for example <span className="font-mono">cam-001:N,cam-002:S</span>. Arm is
          the side traffic arrives from. Cameras with no traffic in the period count as zero.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-72 flex-1">
            <Input
              label="Cameras"
              value={cameras}
              onChange={(e) => setCameras(e.target.value)}
              placeholder="cam-001:N,cam-002:S,cam-003:E,cam-004:W"
            />
          </div>
          <Select label="Period" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            <option value={1}>Last hour</option>
            <option value={3}>Last 3 hours</option>
            <option value={24}>Last 24 hours</option>
          </Select>
          <Button onClick={() => void measure()} disabled={busy || !cameras.trim()}>
            {busy ? "Measuring" : "Measure"}
          </Button>
        </div>
        {error && <p className="text-danger text-xs">{error}</p>}
        {result && (
          <div className="space-y-2">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {ARMS.filter((a) => a in result.approach_veh_h).map((a) => (
                <Kpi
                  key={a}
                  label={`Arm ${a} veh/h (x${result.scale_vs_xtraflow_balanced[a] ?? "?"} of balanced)`}
                  value={String(result.approach_veh_h[a])}
                />
              ))}
            </div>
            {result.silent_cameras.length > 0 && (
              <p className="text-xs text-warning">
                No traffic recorded by {result.silent_cameras.join(", ")}; counted as zero.
              </p>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

export function SignalsView() {
  const [summary, setSummary] = useState<SignalSummary | null>(null);
  const [run, setRun] = useState<LatestRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    const [s, r] = await Promise.allSettled([api.signalsSummary(), api.signalsLatestRun()]);
    if (s.status === "rejected") {
      setError(s.reason instanceof Error ? s.reason.message : "Failed to load signal control");
      return;
    }
    setSummary(s.value);
    setRun(r.status === "fulfilled" ? r.value : null);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) return <ErrorState message={error} onRetry={() => void load()} />;
  if (!summary) return <Skeleton className="h-64 w-full" />;

  return (
    <div className="space-y-4 p-4">
      <h1 className="text-lg font-semibold">Signal control</h1>
      {summary.available ? (
        <HeadlinesCard summary={summary} />
      ) : (
        <Card>
          <div className="p-[var(--card-p)] text-sm text-muted">
            XtraFlow results are not available to the API: {summary.reason ?? "unknown reason"}.
          </div>
        </Card>
      )}
      {run?.available ? (
        <RunCard report={run.report} />
      ) : (
        <Card>
          <div className="p-[var(--card-p)] text-sm text-muted">
            No pipeline run yet. Run <span className="font-mono">crossflow run</span> to measure demand from the
            simulated city and compare signal controllers on it.
          </div>
        </Card>
      )}
      <LiveDemandCard />
    </div>
  );
}
