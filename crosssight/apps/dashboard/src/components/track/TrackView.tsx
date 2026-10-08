"use client";

import { PathStyleExtension } from "@deck.gl/extensions";
import { TripsLayer } from "@deck.gl/geo-layers";
import { PathLayer, ScatterplotLayer } from "@deck.gl/layers";
import { Copy } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { DeckMap, flyTo } from "@/components/map/DeckMap";
import { LoadingState } from "@/components/common/LoadingState";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Timestamp } from "@/components/ui/Timestamp";
import { api } from "@/lib/api";
import { toast } from "@/lib/toast";
import { formatConfidence, formatIstTime, formatTsWithZone } from "@/lib/utils";
import type { GeoJSONFeatureCollection } from "@/types";
import type { Map as MapLibreMap } from "maplibre-gl";

interface Sighting {
  ts: string;
  camera_id: string;
  direction?: string;
  confidence: number;
  crop_key?: string;
  coordinates: [number, number];
}

interface TripPath {
  path: [number, number][];
  timestamps: number[];
  observed: boolean;
  feasible: boolean;
  impossible_hop: boolean;
  from_camera: string;
  to_camera: string;
}

type Preset = "1h" | "24h" | "7d" | "30d" | "custom";

function toInputValue(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 16);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function presetRange(id: Exclude<Preset, "custom">): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to);
  if (id === "1h") from.setHours(from.getHours() - 1);
  if (id === "24h") from.setHours(from.getHours() - 24);
  if (id === "7d") from.setDate(from.getDate() - 7);
  if (id === "30d") from.setDate(from.getDate() - 30);
  return { from: toInputValue(from.toISOString()), to: toInputValue(to.toISOString()) };
}

export function TrackView() {
  const params = useSearchParams();
  const mapRef = useRef<MapLibreMap | null>(null);
  const initialPreset: Preset = params.get("from") || params.get("to") ? "custom" : "7d";
  const initialRange = presetRange("7d");

  const [plate, setPlate] = useState(() => params.get("plate") ?? "");
  const [caseId, setCaseId] = useState(() => params.get("case") ?? "");
  const [from, setFrom] = useState(() => (params.get("from") ? toInputValue(params.get("from") as string) : initialRange.from));
  const [to, setTo] = useState(() => (params.get("to") ? toInputValue(params.get("to") as string) : initialRange.to));
  const [fuzzy, setFuzzy] = useState(() => params.get("fuzzy") === "1");
  const [preset, setPreset] = useState<Preset>(initialPreset);
  const [data, setData] = useState<GeoJSONFeatureCollection | null>(null);
  const [loading, setLoading] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [timeFrac, setTimeFrac] = useState(1);
  const [cropUrls, setCropUrls] = useState<Record<string, string>>({});

  const search = useCallback(async () => {
    if (!plate.trim() || !caseId.trim()) {
      toast.error("Plate and case ID are required");
      return;
    }
    setLoading(true);
    try {
      const result = await api.trajectory({
        plate: plate.trim(),
        case_id: caseId.trim(),
        from: from ? new Date(from).toISOString() : undefined,
        to: to ? new Date(to).toISOString() : undefined,
        fuzzy,
      });
      setData(result);
      setTimeFrac(1);
      setPlaying(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Trajectory query failed");
    } finally {
      setLoading(false);
    }
  }, [plate, caseId, from, to, fuzzy]);

  useEffect(() => {
    if (params.get("plate") && params.get("case")) void search();
    // Reopen a bookmarked query once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { sightings, trips, timeRange } = useMemo(() => {
    if (!data) return { sightings: [] as Sighting[], trips: [] as TripPath[], timeRange: [0, 1] as [number, number] };

    const sightingList: Sighting[] = [];
    const tripList: TripPath[] = [];

    for (const feature of data.features) {
      const props = feature.properties ?? {};
      const geom = feature.geometry;
      if (geom.type === "Point" && props.kind === "sighting") {
        const coords = geom.coordinates as [number, number];
        sightingList.push({
          ts: String(props.ts),
          camera_id: String(props.camera_id),
          direction: props.direction ? String(props.direction) : undefined,
          confidence: Number(props.confidence ?? 0),
          crop_key: props.crop_key ? String(props.crop_key) : undefined,
          coordinates: coords,
        });
      }
      if (geom.type === "LineString" && props.kind === "leg") {
        const coords = geom.coordinates as [number, number][];
        const ts0 = sightingList.find((sighting) => sighting.camera_id === props.from_camera)?.ts;
        const ts1 = sightingList.find((sighting) => sighting.camera_id === props.to_camera)?.ts;
        const t0 = ts0 ? new Date(ts0).getTime() : Date.now();
        const t1 = ts1 ? new Date(ts1).getTime() : t0 + 60000;
        tripList.push({
          path: coords,
          timestamps: [t0, t1],
          observed: Boolean(props.observed),
          feasible: Boolean(props.feasible),
          impossible_hop: Boolean(props.impossible_hop),
          from_camera: String(props.from_camera),
          to_camera: String(props.to_camera),
        });
      }
    }

    sightingList.sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime());
    const times = sightingList.map((sighting) => new Date(sighting.ts).getTime());
    const min = times[0] ?? 0;
    const max = times[times.length - 1] ?? min + 1;
    return { sightings: sightingList, trips: tripList, timeRange: [min, max] as [number, number] };
  }, [data]);

  useEffect(() => {
    const keys = sightings.map((sighting) => sighting.crop_key).filter(Boolean) as string[];
    if (keys.length === 0) {
      setCropUrls({});
      return;
    }
    let cancelled = false;
    (async () => {
      const next: Record<string, string> = {};
      await Promise.all(
        keys.slice(0, 40).map(async (key) => {
          try {
            const res = await api.cropUrl(key);
            next[key] = res.url;
          } catch {
            // crop may be missing for simulator-only reads
          }
        }),
      );
      if (!cancelled) setCropUrls(next);
    })();
    return () => {
      cancelled = true;
    };
  }, [sightings]);

  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    let last = performance.now();
    const step = (now: number) => {
      const dt = now - last;
      last = now;
      setTimeFrac((value) => {
        const next = value + (dt / 30000) * speed;
        if (next >= 1) {
          setPlaying(false);
          return 1;
        }
        return next;
      });
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [playing, speed]);

  const currentTime = timeRange[0] + (timeRange[1] - timeRange[0]) * timeFrac;

  const layers = useMemo(() => {
    if (!data) return [];
    const dashExt = new PathStyleExtension({ dash: true });
    const observedTrips = trips.filter((trip) => trip.observed);
    const tripLayer = new TripsLayer<TripPath>({
      id: "trips",
      data: observedTrips,
      getPath: (d) => d.path,
      getTimestamps: (d) => d.timestamps,
      getColor: (d) => (d.impossible_hop ? [239, 68, 68, 230] : [56, 189, 248, 220]),
      getWidth: 5,
      trailLength: 600000,
      currentTime,
      opacity: 0.9,
    });
    const inferredLayer = new PathLayer<TripPath>({
      id: "inferred-legs",
      data: trips.filter((trip) => !trip.observed),
      getPath: (d) => d.path,
      getColor: (d) => (d.impossible_hop ? [239, 68, 68, 230] : [148, 163, 184, 200]),
      getWidth: 4,
      extensions: [dashExt],
      getDashArray: [4, 4] as [number, number],
      dashJustified: true,
    } as ConstructorParameters<typeof PathLayer<TripPath>>[0]);
    const points = new ScatterplotLayer<Sighting>({
      id: "sightings",
      data: sightings.filter((sighting) => new Date(sighting.ts).getTime() <= currentTime),
      getPosition: (d) => d.coordinates,
      getRadius: 60,
      radiusMinPixels: 5,
      getFillColor: [34, 197, 94, 220],
      pickable: true,
    });
    return [inferredLayer, tripLayer, points];
  }, [data, trips, sightings, currentTime]);

  function applyPreset(next: Preset) {
    setPreset(next);
    if (next === "custom") return;
    const range = presetRange(next);
    setFrom(range.from);
    setTo(range.to);
  }

  function jumpTo(sighting: Sighting) {
    const ts = new Date(sighting.ts).getTime();
    const span = timeRange[1] - timeRange[0] || 1;
    setTimeFrac(Math.min(1, Math.max(0, (ts - timeRange[0]) / span)));
    setPlaying(false);
    if (mapRef.current) flyTo(mapRef.current, sighting.coordinates[0], sighting.coordinates[1], 15);
  }

  async function copyLink() {
    const url = new URL(window.location.href);
    url.pathname = "/track";
    url.search = "";
    url.searchParams.set("plate", plate.trim());
    url.searchParams.set("case", caseId.trim());
    if (from) url.searchParams.set("from", new Date(from).toISOString());
    if (to) url.searchParams.set("to", new Date(to).toISOString());
    url.searchParams.set("fuzzy", fuzzy ? "1" : "0");
    await navigator.clipboard.writeText(url.toString());
    toast.success("Link copied");
  }

  const generated = formatTsWithZone(new Date().toISOString()).primary;

  return (
    <div className="h-full flex flex-col">
      <div className="print-only px-4 py-3">
        <p>CrossSight — Trajectory Report</p>
        <p>Plate: {plate || "—"}  Case: {caseId || "—"}  Range: {from || "—"} → {to || "—"}</p>
        <p>Generated: {generated}</p>
      </div>
      <form
        className="shrink-0 border-b border-border bg-surface-raised px-4 py-3 space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          void search();
        }}
      >
        <div className="flex flex-wrap items-center gap-2">
          {(["1h", "24h", "7d", "30d", "custom"] as Preset[]).map((id) => (
            <button
              key={id}
              type="button"
              className={`rounded border px-2 py-1 text-xs ${preset === id ? "border-accent bg-accent/20" : "border-border text-muted"}`}
              onClick={() => applyPreset(id)}
            >
              {id === "1h" ? "Last hour" : id === "24h" ? "Last 24h" : id === "7d" ? "Last 7d" : id === "30d" ? "Last 30d" : "Custom"}
            </button>
          ))}
          <Button type="button" variant="ghost" size="sm" className="ml-auto" aria-label="Copy link" onClick={() => void copyLink()}>
            <Copy size={14} className="mr-1.5" aria-hidden />
            Copy link
          </Button>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3 items-end">
          <Input label="Plate" value={plate} onChange={(event) => setPlate(event.target.value)} placeholder="BR01AB1234" required />
          <Input label="Case ID" value={caseId} onChange={(event) => setCaseId(event.target.value)} required />
          {preset === "custom" && (
            <>
              <Input label="From" type="datetime-local" value={from} onChange={(event) => setFrom(event.target.value)} />
              <Input label="To" type="datetime-local" value={to} onChange={(event) => setTo(event.target.value)} />
            </>
          )}
          <label className="flex items-center gap-2 text-sm text-muted pb-2">
            <input type="checkbox" checked={fuzzy} onChange={(event) => setFuzzy(event.target.checked)} />
            Fuzzy match
          </label>
          <Button type="submit" disabled={loading}>Search</Button>
        </div>
      </form>

      {loading && <LoadingState label="Reconstructing trajectory…" />}

      {!loading && data && (
        <div className="flex-1 min-h-0 flex">
          <aside className="w-96 border-r border-border overflow-y-auto bg-surface-raised">
            <div className="p-3 border-b border-border text-xs text-muted font-mono">
              {data.summary?.read_count ?? 0} reads · {data.summary?.camera_count ?? 0} cameras ·{" "}
              {data.summary?.impossible_hop_count ?? 0} impossible hops
              {data.summary?.data_status === "widened_to_all_history" ? " · widened to all history" : ""}
            </div>
            <ol className="divide-y divide-border/60">
              {sightings.map((sighting, index) => (
                <li key={`${sighting.ts}-${sighting.camera_id}-${index}`} className="p-3 text-sm">
                  <div className="flex justify-between gap-2">
                    <span className="font-mono text-accent">{sighting.camera_id}</span>
                    <Badge>{formatConfidence(sighting.confidence)}</Badge>
                  </div>
                  <p className="text-muted text-xs mt-1"><Timestamp iso={sighting.ts} /></p>
                  {sighting.direction && <p className="text-xs mt-1">Direction: {sighting.direction}</p>}
                  {sighting.crop_key && cropUrls[sighting.crop_key] && (
                    <img
                      src={cropUrls[sighting.crop_key]}
                      alt={`Plate crop at ${sighting.camera_id}`}
                      className="mt-2 rounded border border-border max-h-16 object-contain bg-black/40"
                    />
                  )}
                </li>
              ))}
            </ol>
          </aside>
          <div className="flex-1 flex flex-col min-w-0">
            <div className="flex-1 relative min-h-0">
              <DeckMap
                layers={layers}
                onMapReady={(map) => { mapRef.current = map; }}
                getTooltip={({ object }) => {
                  if (!object) return null;
                  const hop = object as unknown as TripPath;
                  if (hop.impossible_hop) {
                    return { html: `<b>Impossible hop</b><br/>${hop.from_camera} → ${hop.to_camera}` };
                  }
                  return null;
                }}
              />
            </div>
            <div className="shrink-0 border-t border-border bg-surface-raised px-4 py-2 flex gap-2 overflow-x-auto">
              {sightings.map((sighting, index) => (
                <button
                  key={`${sighting.ts}-${index}`}
                  type="button"
                  className="shrink-0 h-10 w-[60px] rounded border border-border bg-black overflow-hidden"
                  title={`${sighting.camera_id} · ${sighting.ts} · ${formatConfidence(sighting.confidence)}`}
                  onClick={() => jumpTo(sighting)}
                >
                  {sighting.crop_key && cropUrls[sighting.crop_key] ? (
                    <img src={cropUrls[sighting.crop_key]} alt="" className="h-10 w-[60px] object-cover" />
                  ) : (
                    <span className="text-[9px] text-muted">{sighting.camera_id}</span>
                  )}
                </button>
              ))}
            </div>
            <div className="shrink-0 border-t border-border bg-surface-raised px-4 py-3 flex items-center gap-2">
              <Button variant="secondary" size="sm" aria-label="Previous sighting" onClick={() => {
                const previous = [...sightings].reverse().find((sighting) => new Date(sighting.ts).getTime() < currentTime);
                if (previous) jumpTo(previous);
              }}>◀</Button>
              <Button variant="secondary" size="sm" onClick={() => {
                if (timeFrac >= 1) setTimeFrac(0);
                setSpeed(1);
                setPlaying((value) => !value);
              }}>{playing && speed === 1 ? "Pause" : "▶ Play"}</Button>
              <Button variant="secondary" size="sm" onClick={() => { setSpeed(2); setPlaying(true); }}>▶▶ 2x</Button>
              <Button variant="secondary" size="sm" onClick={() => { setSpeed(5); setPlaying(true); }}>▶▶▶ 5x</Button>
              <input
                type="range"
                min={0}
                max={1000}
                value={Math.round(timeFrac * 1000)}
                aria-label="Timeline"
                onChange={(event) => {
                  setTimeFrac(Number(event.target.value) / 1000);
                  setPlaying(false);
                }}
                className="flex-1"
              />
              <span className="text-xs font-mono text-muted w-28 text-right">
                {formatIstTime(new Date(currentTime), true)} IST
              </span>
            </div>
          </div>
        </div>
      )}

      {!loading && !data && (
        <div className="flex-1 p-4">
          <Skeleton className="h-full min-h-64 bg-gradient-to-br from-surface-overlay to-surface" />
        </div>
      )}
    </div>
  );
}
