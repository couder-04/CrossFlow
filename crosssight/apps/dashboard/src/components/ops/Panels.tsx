"use client";

import { PathLayer, ScatterplotLayer } from "@deck.gl/layers";
import { useCallback, useEffect, useMemo, useState } from "react";
import { DeckMap } from "@/components/map/DeckMap";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { CameraStatusDot } from "@/components/ui/CameraStatusDot";
import { Card, CardHeader } from "@/components/ui/Card";
import { Kpi } from "@/components/ui/Kpi";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { api } from "@/lib/api";
import { titleCase } from "@/lib/utils";

function exportHref(id: string) {
  return `/api/backend/exports/${encodeURIComponent(id)}/download`;
}

async function downloadExport(id: string, filename: string) {
  const res = await fetch(exportHref(id));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail ?? body.error;
    const message = typeof detail === "string" ? detail : detail?.message;
    throw new Error(message ?? "Download failed");
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1500);
}

export function ExportButtons({
  kind,
  filters = {},
  onDone,
}: {
  kind: string;
  filters?: Record<string, string>;
  onDone?: () => void;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const [save, setSave] = useState<{ id: string; filename: string } | null>(null);

  async function run(format: "csv" | "pdf" | "json") {
    if (kind === "investigation" && !filters.plate?.trim()) {
      setMessage("Enter a plate first");
      setSave(null);
      return;
    }
    setMessage("Queued");
    setSave(null);
    try {
      const job = await api.createExport(kind, format, filters);
      const id = String(job.id);
      for (let attempt = 0; attempt < 20; attempt += 1) {
        const current = await api.exports();
        const row = current.find((item) => item.id === id);
        if (row?.status === "ready") {
          const filename = String(row.filename ?? `${kind}.${format}`);
          setSave({ id, filename });
          setMessage("Ready");
          onDone?.();
          await downloadExport(id, filename);
          return;
        }
        if (row?.status === "failed") {
          throw new Error(String(row.error ?? "Export failed"));
        }
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      setMessage("Still processing — check Export");
      onDone?.();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Export failed");
      onDone?.();
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" variant="secondary" onClick={() => run("csv")}>Export CSV</Button>
      <Button size="sm" variant="secondary" onClick={() => run("pdf")}>Download PDF</Button>
      <Button size="sm" variant="ghost" onClick={() => run("json")}>JSON</Button>
      {save && (
        <Button size="sm" href={exportHref(save.id)} download={save.filename}>
          Save {save.filename}
        </Button>
      )}
      {message && <span className="text-xs text-muted max-w-md">{message}</span>}
    </div>
  );
}

function Table({ columns, rows }: { columns: string[]; rows: Array<Record<string, unknown>> }) {
  return (
    <div className="overflow-auto">
      <table>
        <thead>
          <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={columns.length} className="text-muted">No rows for the current filters.</td></tr>
          )}
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column}>
                  {column === "state" || column === "status" ? (
                    <span className="inline-flex items-center gap-2">
                      <CameraStatusDot status={String(row[column] ?? "").toLowerCase()} />
                      {String(row[column] ?? "—")}
                    </span>
                  ) : (
                    String(row[column] ?? "—")
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function HealthPanel() {
  const [rows, setRows] = useState<Array<Record<string, unknown>>>([]);
  const [summary, setSummary] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.cameraHealth();
      setRows(data.cameras);
      setSummary(data.summary);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Health query failed");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="h-full overflow-auto p-4 space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Camera health</h1>
        <div className="flex gap-2">
          <Button size="sm" onClick={async () => { await api.scanHealth(); await load(); }}>Scan incidents</Button>
          <ExportButtons kind="camera_health" />
        </div>
      </div>
      {error && <p className="text-danger text-sm">{error}</p>}
      <div className="w-56">
        <Kpi
          variant="hero"
          label="Healthy cameras"
          value={String(summary.HEALTHY ?? summary.healthy ?? 0)}
          tone="success"
        />
      </div>
      <div className="flex gap-2">
        {Object.entries(summary).map(([state, count]) => (
          <Badge key={state} case="normal" tone={state === "HEALTHY" ? "success" : state === "OFFLINE" ? "danger" : "warning"}>
            {titleCase(state.toLowerCase())} {count}
          </Badge>
        ))}
      </div>
      <Card>
        <CardHeader title="Cameras" />
        <Table columns={["camera_id", "state", "last_read", "age_s", "read_rate_per_min"]} rows={rows} />
      </Card>
    </div>
  );
}

export function FlowPanel() {
  const [cells, setCells] = useState<Array<Record<string, unknown>>>([]);
  const [routes, setRoutes] = useState<Array<Record<string, unknown>>>([]);
  const [vehicles, setVehicles] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.cameraOd(), api.travel(), api.vehicles()])
      .then(([od, travel, fleet]) => {
        setCells(od.cells);
        setRoutes(travel.routes);
        setVehicles(fleet);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Flow query failed"));
  }, []);

  const layers = useMemo(() => {
    const paths = cells
      .filter((cell) => cell.origin_lng != null && cell.destination_lng != null)
      .map((cell) => ({
        path: [
          [Number(cell.origin_lng), Number(cell.origin_lat)],
          [Number(cell.destination_lng), Number(cell.destination_lat)],
        ] as [number, number][],
        trips: Number(cell.trip_count),
      }));
    return [
      new PathLayer({
        id: "od-flow",
        data: paths,
        getPath: (d: { path: [number, number][] }) => d.path,
        getWidth: (d: { trips: number }) => 2 + Math.min(d.trips, 12),
        getColor: [56, 189, 248, 180],
        widthMinPixels: 2,
      }),
      new ScatterplotLayer({
        id: "od-nodes",
        data: cells.flatMap((cell) => [
          { position: [Number(cell.origin_lng), Number(cell.origin_lat)] },
          { position: [Number(cell.destination_lng), Number(cell.destination_lat)] },
        ]).filter((point) => Number.isFinite(point.position[0])),
        getPosition: (d: { position: number[] }) => d.position as [number, number],
        getFillColor: [248, 250, 252, 220],
        radiusMinPixels: 5,
      }),
    ];
  }, [cells]);

  const counts = (vehicles.counts ?? {}) as Record<string, number>;

  return (
    <div className="h-full flex flex-col">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Traffic flow</h1>
        <ExportButtons kind="od" />
      </div>
      {error && <p className="text-danger text-sm px-4 py-2">{error}</p>}
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-2 min-h-0">
        <DeckMap layers={layers} />
        <div className="overflow-auto p-4 space-y-4">
          <Card>
            <CardHeader title="Origin destination" action={<ExportButtons kind="flow" />} />
            <Table columns={["origin", "destination", "trip_count", "unique_vehicles"]} rows={cells} />
          </Card>
          <Card>
            <CardHeader title="Travel time" action={<ExportButtons kind="travel" />} />
            <Table columns={["origin", "destination", "count", "median_s", "avg_s", "p90_s", "anomaly"]} rows={routes} />
          </Card>
          <Card>
            <CardHeader title="Vehicle classes" action={<ExportButtons kind="vehicles" />} />
            <Table
              columns={["class", "count"]}
              rows={Object.entries(counts).map(([klass, count]) => ({ class: klass, count }))}
            />
          </Card>
        </div>
      </div>
    </div>
  );
}

export function ReviewPanel() {
  const [alerts, setAlerts] = useState<Array<Record<string, unknown>>>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const rows = await api.alerts();
      setAlerts(rows as unknown as Array<Record<string, unknown>>);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the queue");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function act(id: string, status: string) {
    try {
      await api.reviewAlert(id, status, note || "reviewed");
      setNote("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Review failed");
    }
  }

  return (
    <div className="h-full overflow-auto p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Enforcement review</h1>
        <ExportButtons kind="enforcement" />
      </div>
      {error && <p className="text-danger text-sm">{error}</p>}
      <Input label="Review note" value={note} onChange={(event) => setNote(event.target.value)} />
      <Card>
        <CardHeader title="Queue" />
        <div className="divide-y divide-border">
          {alerts.map((alert) => (
            <div key={String(alert.id)} className="p-3 flex flex-wrap items-center gap-3 text-sm">
              <span className="font-mono text-accent">{String(alert.plate_norm)}</span>
              <Badge>{String(alert.type)}</Badge>
              <span className="text-muted">{String(alert.status)}</span>
              <Button size="sm" variant="secondary" onClick={() => act(String(alert.id), "reviewing")}>Review</Button>
              <Button size="sm" onClick={() => act(String(alert.id), "approved")}>Approve</Button>
              <Button size="sm" variant="ghost" onClick={() => act(String(alert.id), "dismissed")}>Dismiss</Button>
              <Button size="sm" variant="danger" onClick={() => act(String(alert.id), "closed")}>Close</Button>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

const IMPORTS = [
  ["watchlist", "Watchlist CSV"],
  ["registry", "Vehicle registry CSV"],
  ["cameras", "Camera config CSV/JSON"],
  ["calibration", "Calibration JSON"],
  ["zones", "Zones GeoJSON"],
] as const;

const EXPORTS = [
  ["camera_health", "Camera health"],
  ["od", "Origin / destination"],
  ["flow", "Flow"],
  ["travel", "Travel time"],
  ["dwell", "Dwell"],
  ["vehicles", "Vehicle classes"],
  ["traffic", "Traffic"],
  ["incidents", "Incidents"],
  ["enforcement", "Enforcement"],
  ["sightings", "Sightings"],
  ["investigation", "Investigation"],
  ["watchlist", "Watchlist"],
  ["registry", "Vehicle registry"],
] as const;

export function ImportPanel() {
  const [kind, setKind] = useState("watchlist");
  const [mediaKind, setMediaKind] = useState("video");
  const [cameraId, setCameraId] = useState("");
  const [capturedAt, setCapturedAt] = useState("");
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [history, setHistory] = useState<Array<Record<string, unknown>>>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [acknowledge, setAcknowledge] = useState(false);
  const [overwrite, setOverwrite] = useState(false);

  const load = useCallback(async () => {
    setHistory(await api.imports());
  }, []);
  useEffect(() => { load().catch((err) => setMessage(err instanceof Error ? err.message : "History failed")); }, [load]);

  return (
    <div className="h-full overflow-auto p-4 space-y-4">
      <h1 className="text-lg font-semibold">Upload / import</h1>
      {message && <p className="text-sm text-warning">{message}</p>}
      <div className="grid md:grid-cols-2 gap-4">
        <Card>
          <CardHeader title="CCTV or plate image" />
          <div className="p-4 space-y-3">
            <Select label="Media" value={mediaKind} onChange={(event) => setMediaKind(event.target.value)}>
              <option value="video">CCTV video</option>
              <option value="image">Image / plate frame</option>
            </Select>
            <Input label="Camera id" value={cameraId} onChange={(event) => setCameraId(event.target.value)} />
            <Input label="Captured at (ISO)" value={capturedAt} onChange={(event) => setCapturedAt(event.target.value)} />
            <input
              type="file"
              accept={mediaKind === "video" ? ".mp4,.mov,.avi,.mkv,.webm" : ".jpg,.jpeg,.png,.webp"}
              onChange={async (event) => {
                const file = event.target.files?.[0];
                if (!file || !cameraId) {
                  setMessage("Choose a camera and a file");
                  return;
                }
                try {
                  const job = await api.uploadMedia(mediaKind, cameraId, file, capturedAt);
                  setMessage(`Queued ${job.id} (${job.status})`);
                  await load();
                } catch (err) {
                  setMessage(err instanceof Error ? err.message : "Upload failed");
                }
              }}
            />
          </div>
        </Card>
        <Card>
          <CardHeader title="Table or map import" />
          <div className="p-4 space-y-3">
            <Select label="Kind" value={kind} onChange={(event) => setKind(event.target.value)}>
              {IMPORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </Select>
            <input
              type="file"
              accept=".csv,.json,.geojson"
              onChange={async (event) => {
                const file = event.target.files?.[0];
                if (!file) return;
                try {
                  const result = await api.previewImport(kind, file);
                  setPreview(result);
                  setAcknowledge(false);
                  setMessage(`${result.valid_count ?? 0} valid, ${result.invalid_count ?? 0} invalid, ${result.duplicate_count ?? 0} duplicates`);
                  await load();
                } catch (err) {
                  setMessage(err instanceof Error ? err.message : "Validation failed");
                }
              }}
            />
            <label className="text-sm flex gap-2"><input type="checkbox" checked={acknowledge} onChange={(event) => setAcknowledge(event.target.checked)} /> Acknowledge invalid rows</label>
            <label className="text-sm flex gap-2"><input type="checkbox" checked={overwrite} onChange={(event) => setOverwrite(event.target.checked)} /> Overwrite existing cameras</label>
            <div className="flex gap-2">
              <Button
                size="sm"
                disabled={!preview?.id}
                onClick={async () => {
                  try {
                    await api.confirmImport(String(preview?.id), acknowledge, overwrite);
                    setMessage("Import committed");
                    setPreview(null);
                    await load();
                  } catch (err) {
                    setMessage(err instanceof Error ? err.message : "Confirm failed");
                  }
                }}
              >Confirm import</Button>
              <Button size="sm" variant="ghost" onClick={() => setPreview(null)}>Cancel</Button>
            </div>
          </div>
        </Card>
      </div>
      {preview && (
        <Card>
          <CardHeader title="Validation preview" />
          <pre className="p-4 text-xs overflow-auto max-h-64">{JSON.stringify(preview.preview ?? preview, null, 2)}</pre>
        </Card>
      )}
      <Card>
        <CardHeader title="Import history" />
        <Table columns={["id", "filename", "kind", "uploaded_by", "created_at", "status", "valid_count", "invalid_count", "error"]} rows={history} />
        <div className="p-3 flex flex-wrap gap-2">
          {history.filter((row) => row.status === "failed" && (row.kind === "video" || row.kind === "image")).map((row) => (
            <Button
              key={String(row.id)}
              size="sm"
              variant="secondary"
              onClick={async () => {
                try {
                  await api.retryUpload(String(row.id));
                  setMessage(`Requeued ${String(row.filename)}`);
                  await load();
                } catch (err) {
                  setMessage(err instanceof Error ? err.message : "Retry failed");
                }
              }}
            >
              Retry {String(row.filename)}
            </Button>
          ))}
        </div>
      </Card>
    </div>
  );
}

export function ExportPanel() {
  const [rows, setRows] = useState<Array<Record<string, unknown>>>([]);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState("camera_health");
  const [plate, setPlate] = useState("");
  const load = useCallback(async () => {
    try {
      setRows(await api.exports());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load exports");
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  const filters: Record<string, string> =
    kind === "investigation" && plate.trim() ? { plate: plate.trim() } : {};
  return (
    <div className="h-full overflow-auto p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Export / reports</h1>
        <Button size="sm" variant="secondary" onClick={load}>Refresh</Button>
      </div>
      {error && <p className="text-danger text-sm">{error}</p>}
      <Card>
        <CardHeader title="New report" />
        <div className="p-4 flex flex-wrap items-end gap-3">
          <Select label="Report" value={kind} onChange={(event) => setKind(event.target.value)}>
            {EXPORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </Select>
          {kind === "investigation" && (
            <Input label="Plate" value={plate} onChange={(event) => setPlate(event.target.value)} />
          )}
          <ExportButtons kind={kind} filters={filters} onDone={load} />
        </div>
      </Card>
      <Card>
        <CardHeader title="Jobs" />
        <div className="divide-y divide-border">
          {rows.map((row) => (
            <div key={String(row.id)} className="p-3 flex flex-wrap gap-3 items-center text-sm">
              <span className="font-mono">{String(row.kind)}</span>
              <Badge>{String(row.format)}</Badge>
              <span>{String(row.status)}</span>
              <span className="text-muted">{String(row.requested_by)} · {String(row.requested_at ?? "")}</span>
              {row.status === "ready" && (
                <Button
                  size="sm"
                  href={exportHref(String(row.id))}
                  download={String(row.filename ?? "export")}
                >
                  Download
                </Button>
              )}
              {row.status === "failed" && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={async () => {
                    try {
                      await api.retryExport(String(row.id));
                      await load();
                    } catch (err) {
                      setError(err instanceof Error ? err.message : "Retry failed");
                    }
                  }}
                >
                  Retry
                </Button>
              )}
              {row.error != null && row.error !== "" && (
                <span className="text-danger text-xs max-w-xl">{String(row.error)}</span>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

export function InvestigatePanel() {
  const [plate, setPlate] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sightings = (result?.sightings as Array<Record<string, unknown>> | undefined) ?? [];
  return (
    <div className="h-full overflow-auto p-4 space-y-4">
      <h1 className="text-lg font-semibold">Investigation</h1>
      <div className="flex gap-2 items-end">
        <Input label="Plate" value={plate} onChange={(event) => setPlate(event.target.value)} />
        <Button onClick={async () => {
          try {
            setResult(await api.investigation(plate));
            setError(null);
          } catch (err) {
            setError(err instanceof Error ? err.message : "Search failed");
          }
        }}>Search</Button>
        <ExportButtons kind="investigation" filters={{ plate }} />
      </div>
      {error && <p className="text-danger text-sm">{error}</p>}
      <Card>
        <CardHeader title="Sightings" />
        <Table columns={["ts", "camera_id", "plate_norm", "confidence", "vehicle_class"]} rows={sightings} />
      </Card>
    </div>
  );
}
