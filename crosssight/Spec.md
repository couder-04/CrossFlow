# Build: City-wide ANPR Intelligence Platform (MVP, runnable end-to-end)

You are a senior engineer building a complete, runnable monorepo in one session. Read this entire spec before writing any code. Work autonomously through every phase until the Definition of Done passes. Do not ask me questions; record assumptions in `PLAN.md` and proceed.

## 0. Goal and scope

Build a centralized platform that ingests license-plate reads from a city-wide camera network and provides:

1. **OCR engine**: a video pipeline that detects vehicles and plates, reads Indian plates, fuses reads across frames, and publishes events. It includes an evaluation harness that measures plate-level accuracy per condition.
2. **Trajectory reconstruction**: a query API plus map playback of any plate's path across the city.
3. **Traffic analytics**: density, segment speeds, origin-destination (OD) matrices, congestion, and live heatmaps.
4. **Alert system**: watchlist hits and route anomalies in real time, with an operator workflow.
5. **GIS dashboard** covering all of the above.

There is no real camera network, so a **simulator** generates realistic read events on a real OpenStreetMap road graph. The whole platform must run on simulated events with no video input. The OCR engine is a separate CLI/service that emits the same event schema from real video files or RTSP streams.

**Non-negotiables**

- Never hardcode, fabricate, or claim accuracy numbers. Accuracy is only what `make eval` measures.
- No `TODO`, `pass`, or `NotImplementedError` in required paths. Only the items marked **[STUB OK]** may be stubbed, and each stub must sit behind an interface, have a working no-op implementation, and be documented in the README.
- Everything starts with `docker compose up` on a CPU-only machine (WSL2-compatible). GPU is an optional compose profile.
- Model weights are never committed. `models/` is gitignored.

## 1. Tech stack (use these, latest stable, pin in lockfiles)

- **Python 3.11** managed with `uv` workspaces. Use `ruff`, `pytest`, Pydantic v2, FastAPI, `aiokafka`, `clickhouse-connect`, `asyncpg`/SQLAlchemy 2, `redis`, `osmnx`, `networkx`, `h3` (v4 API: `latlng_to_cell` etc.), `rapidfuzz`, `ultralytics`, `torch`, `opencv-python-headless`, `albumentations`.
- **Event bus:** Redpanda (Kafka API) in Docker.
- **Storage:**
  - ClickHouse for reads and aggregates
  - PostgreSQL 16 + PostGIS for cameras, zones, watchlist, alerts, users and audit
  - Redis for hot state
  - MinIO for plate crops
- **Dashboard:** Next.js (App Router) + TypeScript, pnpm, Tailwind, shadcn/ui, MapLibre GL, deck.gl (`@deck.gl/geo-layers`, `@deck.gl/extensions`, `@deck.gl/mapbox` overlay), recharts. The map style URL is configurable via env, with a free public style as the default.
- **Tooling:** a top-level `Makefile` and `docker-compose.yml`.

## 2. Repository layout

```
anpr-platform/
  PLAN.md                     # your phase checklist + assumptions (keep updated)
  README.md
  Makefile
  docker-compose.yml          # profiles: default (cpu), gpu
  .env.example
  infra/
    clickhouse/init.sql
    postgres/init.sql
    redpanda/topics.sh
  packages/
    anpr_common/              # pydantic schemas, plate grammar, fuzzy matching, config, geo utils
  services/
    ocr_engine/               # video pipeline, fusion, recognizer, eval, training scripts
    simulator/                # OSM graph, virtual cameras, trip generation, scenario injection
    workers/                  # ingest, trajectory indexer, analytics, alert engine
    api/                      # FastAPI REST + WebSocket
  apps/
    dashboard/                # Next.js
  scripts/                    # generate TS types from JSON Schema, seed data
  tests/integration/
  models/                     # gitignored
```

## 3. Shared contracts (`packages/anpr_common`)

### 3.1 Event schema: `PlateRead` (topic `anpr.reads.v1`, message key = `plate_norm`)

```
event_id: UUID
camera_id: str
ts: datetime (UTC, ms precision)
plate_raw: str
plate_norm: str            # grammar-normalized, uppercase, no spaces
plate_valid: bool
plate_format: "standard" | "bh" | "nonstandard"
confidence: float          # fused plate-level confidence 0..1
char_conf: list[float]
alternates: list[str]      # top-k fused alternatives
lane: int | None
direction: "N"|"NE"|"E"|"SE"|"S"|"SW"|"W"|"NW" | None
vehicle_class: "car"|"motorcycle"|"bus"|"truck"|"auto"|"other"
color: str | None
make: str | None
speed_kmh: float | None
crop_key: str | None       # MinIO object key
source: "ocr" | "simulator"
```

Other topics:
- `alerts.v1` carries an `Alert` model.
- `analytics.flow.v1` carries a `FlowWindow` model.

Export JSON Schema for all models. Generate TypeScript types into `apps/dashboard/src/types/` with a script that runs in `make types`.

### 3.2 Indian plate grammar (`grammar.py`), which must be exhaustively unit-tested

**Formats**

| Format | Regex | Example |
|---|---|---|
| Standard | `^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$` | `BR01AB1234`, `DL3CAB1234` |
| BH series | `^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$` | `22BH1234AA` |

**State and UT codes**

- Validate the first two letters against a whitelist of current codes.
- Also accept legacy codes: OR, UA, DN, and both TS and TG.

**Correction**

- Apply position-aware confusion correction.
  - In letter positions, map digits to letters: 0→O, 1→I, 2→Z, 5→S, 8→B, 6→G.
  - In digit positions, apply the reverse mapping.
- Only apply a correction when the corrected string becomes valid.
- Return a `GrammarResult(norm, valid, format, corrections: list[(pos, from, to)])`.

**Non-matching strings**

- Strings matching neither format are returned as `nonstandard`.
- Never force-correct them.

### 3.3 Fuzzy plate matching (`fuzzy.py`)

- Use weighted edit distance where substitutions inside confusion pairs cost 0.3 and all others cost 1.0.
- Provide `candidates(plate, max_cost=1.0)` for watchlist and trajectory lookups.

### 3.4 Config

- Use a single Pydantic Settings class that loads from env.
- Settings include:
  - `CITY_QUERY` (an OSMnx place string) or `CITY_BBOX`
  - `NUM_CAMERAS`
  - `MAX_URBAN_SPEED_KMH=120`
  - `TRIP_GAP_MIN=30`
  - `OD_K_ANON=5`
  - `RAW_RETENTION_DAYS=30`
  - H3 resolutions: heatmap res 8, OD res 7

## 4. Infrastructure schemas

### 4.1 ClickHouse

**`anpr_reads`**
- Engine: MergeTree, `PARTITION BY toYYYYMMDD(ts)`, `ORDER BY (plate_norm, ts)`.
- Columns: all `PlateRead` fields, plus `h3_r8 UInt64` and `h3_r7 UInt64`.
- Add an `ngrambf_v1` skip index on `plate_norm`.
- Add `TTL ts + INTERVAL {RAW_RETENTION_DAYS} DAY`.
- Add a projection or materialized view ordered by `(camera_id, ts)` for camera-time queries.

**Other tables**
- `flow_5min`: per camera, lane, 5-minute bin — counts by class and average speed.
- `segment_speed_5min`: per directed camera pair — median travel time, median speed, sample count.
- `od_hourly`: per origin H3, destination H3, hour — trip count.
- `heatmap_1min`: per H3 cell, minute — count.

### 4.2 PostGIS

**`cameras`**
- Columns: `id`, `name`, `geom Point 4326`, `heading_deg`, `lanes`, `allowed_direction`, `osm_u`, `osm_v`, `status`.

**`zones`**
- Columns: `id`, `name`, `kind` (sensitive | restricted | ward), `geom Polygon`, `active_hours`.

**`watchlist`**
- Columns: `plate_norm`, `reason`, `severity`, `added_by`, `expires_at`.

**`alerts`**
- Columns: `id`, `type`, `severity`, `plate_norm`, `camera_ids[]`, `evidence jsonb`, `status`, `ack_by`, `dispatched_to`, `closed_note`, timestamps.
- `status` is one of: `new`, `acknowledged`, `dispatched`, `closed`, `false_positive`.

**`users`**
- Columns: `id`, `username`, `password_hash`, `role` (admin | operator | analyst).

**`audit_log`**
- Columns: `user_id`, `action`, `plate_norm`, `case_id`, `params jsonb`, `ts`.

**`camera_pairs`**
- Precomputed network distance in metres between every pair of cameras, along the road graph, and whether a pair is "adjacent" (no other camera on the shortest path between them).

### 4.3 Redis

Keys:
- `watchlist:set`
- `lastseen:{plate}` (hash of camera_id and ts)
- per-camera recent-plates sorted sets for convoy detection
- co-occurrence counters with TTL

Pub/sub is used for dashboard fan-out.

## 5. Phases (implement in this order; tick them off in PLAN.md)

### Phase 1: Foundations

**Deliverables**
- `docker-compose.yml` with Redpanda, ClickHouse, PostGIS, Redis and MinIO, all with healthchecks.
- Init scripts for all three data stores.
- `anpr_common` with the schemas, grammar, fuzzy matching and config from Section 3.
- `make up`, `make down`, `make types`.

**Tests**
- Grammar: at least 40 cases, including BH-series plates, legacy codes, two-digit vs one-digit RTO numbers, Delhi category letters, confusion corrections, and nonstandard rejects.
- Fuzzy matching.
- Schema round-trip.

### Phase 2: Simulator (`services/simulator`)

**Road graph and cameras**
1. Download the drivable OSM graph for `CITY_QUERY`, cached to GraphML.
2. If the download fails (offline), fall back to a bundled synthetic 20×20 grid graph.
3. Place `NUM_CAMERAS` (default 60) at the intersections with the highest betweenness centrality. Assign heading and lane count.
4. Seed PostGIS with the cameras, precompute `camera_pairs`, and create zones:
   - ward zones from an H3 res-7 cover
   - 2 sensitive zones
   - 1 restricted zone

**Vehicle population and trips**
- Generate `NUM_VEHICLES` (default 3000) vehicles with valid Indian plates, drawn from a weighted mix of state codes, plus a few BH-series plates.
- Give each vehicle home and work zones.
- Drive demand with a time-of-day curve (morning and evening peaks).
- Each trip follows the shortest path. Edge travel time comes from the OSM speed or road-type default, multiplied by a congestion factor that rises at peaks on high-centrality edges.

**Emitting reads**
- Emit a `PlateRead` when a vehicle traverses a camera edge.
- Detection probability is 0.92.
- Inject OCR noise on 6% of reads: a confusion-pair substitution or a single random character error.
- Set `confidence` accordingly and `source="simulator"`.

**Scripted scenarios** (recorded in a `scenarios.json` ground-truth file):
- 10 watchlisted plates
- 2 cloned-plate pairs: the same plate at two distant places within impossible times
- 1 convoy: 3 vehicles within 15 s of each other at 4 or more consecutive cameras
- 1 loiterer: 6 passes near a sensitive zone within 45 minutes
- 1 wrong-way event
- 1 restricted-zone entry at night

**Run modes**
- `simulate --speed 60x` for live mode.
- `simulate --backfill-days 7` writes history fast so analytics baselines exist.

**Makefile targets**
- `make seed`, `make simulate`, `make backfill`.

### Phase 3: Stream workers (`services/workers`)

Each worker is an independent async `aiokafka` consumer with graceful shutdown and a `/healthz` endpoint.

**1. `ingest`**
- Validate each read and drop duplicates: same plate at the same camera within 10 s.
- Enrich with the H3 cells.
- Batch-insert into ClickHouse (every 1 s or 5k rows).
- Update `lastseen`.
- Publish heatmap increments to Redis pub/sub.

**2. `analytics`**
- Maintain 5-minute tumbling windows, which feed `flow_5min`.
- Segment speeds:
  - When a plate is seen at camera A and then at an adjacent camera B, travel time = t_B − t_A and speed = pair distance / travel time.
  - Drop samples that are too fast (above `MAX_URBAN_SPEED_KMH`) or too slow (above 3× the pair's median travel time, which suggests a stop).
  - Aggregate as medians into `segment_speed_5min`.
- OD matrices:
  - Split each plate's sightings into trips wherever the gap exceeds `TRIP_GAP_MIN`. The origin is the H3 cell of the first sighting and the destination is the H3 cell of the last.
  - Write trip counts to `od_hourly`.
  - Do not suppress small cells at write time; suppression happens at query time in the API (Phase 4).
- Congestion index: 1 − current median speed / free-flow speed.
  - Free-flow speed is the 85th-percentile speed during 00:00–05:00 over the backfill.
- Bottleneck: congestion index ≥ 0.5 on a segment while flow at the downstream camera is below 60% of its baseline.
- Volume anomaly: a z-score above 3 against the hour-of-week baseline for that camera.
- Publish `FlowWindow` messages to `analytics.flow.v1`.

**3. `alerts`**
Implement each rule as a class behind a `Rule` interface. Every rule emits an `Alert` with an `evidence` payload listing reads, cameras, and computed values.

| Rule | Logic |
|---|---|
| `WatchlistRule` | Exact match → high severity. Fuzzy candidate (cost ≤ 1) → medium severity with `needs_verification=true`. |
| `ClonedPlateRule` | Required speed between the new read and `lastseen` is above `MAX_URBAN_SPEED_KMH` × 1.5 over the network distance, and both reads have confidence ≥ 0.8. |
| `ConvoyRule` | Two or more other plates appear within 20 s of this plate at 3 or more common cameras within 30 minutes. |
| `LoiteringRule` | 5 or more sightings at cameras inside a buffered sensitive zone within 60 minutes. |
| `GeofenceRule` | A read inside a restricted zone outside its `active_hours`. |
| `WrongWayRule` | Read direction is the opposite of the camera's `allowed_direction`. |
| `PlateVehicleMismatchRule` **[STUB OK]** | Takes a `RegistryClient` interface. The no-op implementation returns unknown. Document where a Vahan integration would plug in. |

**Alert handling**
- Deduplicate alerts per (type, plate) within 10 minutes.
- Persist alerts to PostGIS, publish them to `alerts.v1`, and publish them to Redis pub/sub.

### Phase 4: API (`services/api`)

**General**
- FastAPI with an OpenAPI schema.
- JWT authentication with RBAC and a seeded user for each role (default credentials in `.env.example`).
- CORS enabled for the dashboard.

**Endpoints**
- `POST /auth/login`

- `GET /cameras`, plus admin CRUD for cameras.
- `GET /zones`, plus admin CRUD for zones.
- `GET /watchlist`, `POST` / `DELETE` entries, and `POST /watchlist/import` for CSV upload.

- `GET /trajectory?plate=&from=&to=&fuzzy=&case_id=` (operator and admin only). Behavior:
  1. `case_id` is required, and every call is written to `audit_log`.
  2. Fetch the plate's reads. If `fuzzy=true`, include candidates, but merge a candidate's reads only when its `vehicle_class` and `color` are consistent with the main plate.
  3. Sort the reads by time.
  4. Flag impossible hops.
  5. Gap-fill between consecutive non-adjacent cameras with a time-feasible shortest path: use the graph weighted by historical segment travel times for that hour, falling back to free-flow speeds.
  6. Return a GeoJSON `FeatureCollection` containing:
     - sightings as Points with ts, camera and confidence
     - legs as LineStrings with `observed: bool`, `feasible: bool` and `eta_s`
     - a `summary` object: distance, duration, and counts of cameras, reads and flags

- `GET /analytics/heatmap?window=15m`
- `GET /analytics/flow?camera_id=&from=&to=`
- `GET /analytics/segments?at=` (congestion index per segment)
- `GET /analytics/od?hour=&date=`
  - Suppress any cell with fewer than `OD_K_ANON` trips.
  - Analysts are allowed to call this endpoint.
- `GET /analytics/bottlenecks`
- `GET /analytics/anomalies`

- `GET /alerts?status=&type=&severity=`
- `POST /alerts/{id}/ack`
- `POST /alerts/{id}/dispatch`
- `POST /alerts/{id}/close`
- `GET /alerts/{id}`: includes a presigned MinIO crop URL when a crop exists.

- `GET /audit` (admin only)

- `WS /ws/live`: multiplexed channels `heatmap`, `alerts`, `flow`, fed from Redis pub/sub.

**Role restrictions**
- Analysts can never access plate-level endpoints.
- Enforce this and test it.

### Phase 5: Dashboard (`apps/dashboard`)

**General**
- Clean, dense control-room UI with a dark theme by default.
- Login page, with a role-aware navigation bar.

**Pages**

*`/live`*
- Full-screen map.
- Layers:
  - H3 heatmap (deck.gl `H3HexagonLayer`, live over WebSocket)
  - camera markers colored by status and volume
  - segment `PathLayer` colored by congestion index
- A right rail shows live alert toasts and city KPIs: vehicles in the last 15 minutes, average speed, and number of congested segments.

*`/track`*
- Search form: plate, time range, fuzzy toggle, and a **required** case ID.
- Result layout:
  - left: a chronological timeline of sightings (time, camera, direction, confidence, crop thumbnail)
  - right: map with a `TripsLayer` playback, controlled by a play/pause button and a time slider
- Observed legs are drawn solid. Inferred legs are dashed, using `PathStyleExtension`.
- Impossible hops are shown in red with a tooltip.

*`/analytics`*
- OD `ArcLayer` with an hour slider and a zone filter.
- Volume and speed time-series for a selected camera or segment (recharts).
- Bottleneck table with a click-through that zooms the map.
- Anomaly list.

*`/alerts`*
- Filterable live table.
- Detail drawer showing:
  - an evidence mini-map
  - the reads involved
  - the crop image
  - a fuzzy-match warning banner when `needs_verification` is set
  - acknowledge, dispatch and close actions (close requires a note)

*`/admin`*
- Management tables for cameras, zones and the watchlist (with CSV import).
- Audit log viewer.

**Engineering requirements**
- Typed API client generated from the OpenAPI schema, or hand-written against the generated types.
- Loading and error states on every data view.
- No browser storage for authentication beyond an httpOnly cookie set by a Next.js route handler.

### Phase 6: OCR engine (`services/ocr_engine`)

**Pipeline (`pipeline.py`)**
Source: a video file or RTSP stream, read with OpenCV or PyAV.
1. **Vehicle detection:** ultralytics YOLO with COCO weights; keep car, motorcycle, bus and truck.
2. **Tracking:** ultralytics ByteTrack.
3. **Plate detection** on each vehicle crop, using the plate detector weights from `PLATE_DET_WEIGHTS`:
   - If the model is a pose/keypoint model, use its 4 corner points.
   - Otherwise, estimate the corners from the bounding box with a `minAreaRect`.
4. **Rectification:** perspective-warp the plate to a canonical size. Detect two-row plates by aspect ratio.
5. **Quality score** from Laplacian variance, plate pixel height, and exposure.
6. **Enhancement:** always apply CLAHE. Heavy restoration (deblur, super-resolution) runs only through the `Enhancer` interface, gated by low quality or low confidence. **[STUB OK]** for the heavy restoration model itself, but the gating logic must be real and tested.
7. **Recognition** through a `Recognizer` interface:
   - Default implementation: PARSeq loaded via `torch.hub` (`baudm/parseq`), with output restricted to `A–Z0–9`.
   - Also provide a trivial `MockRecognizer` for tests.
8. **Track-level fusion (`fusion.py`)**, performed when a track ends or after N frames:
   - Align all candidate strings to the best candidate using Levenshtein alignment.
   - Vote at each position, weighting by character probability × frame quality score.
   - Produce the fused string, per-character confidences, and top-k alternates.
9. **Grammar normalization** with the Section 3.2 module.
10. **Output:**
    - Upload the plate crop to MinIO.
    - Estimate direction from the track's motion vector and the camera heading.
    - Publish a `PlateRead` with `source="ocr"`.

**CLI**
- `ocr-engine run --source <file|rtsp> --camera-id <id>`
- `--dry-run` prints events instead of publishing them.

**Degraded mode**
If the plate detector weights are missing, log one clear error explaining how to obtain or train them and exit with a non-zero code. The rest of the platform must be unaffected.

**Evaluation (`eval.py`, run with `make eval`)**
- **Input:** a CSV with columns `image_path, gt_plate, tags`. Tags come from: day, night, rain, fog, angle_gt30, blur, dirty, damaged, two_row.
- **Metrics:**
  - primary: plate-level exact-match accuracy, overall and per tag
  - secondary: character accuracy
- **Report:** confusion pairs and a list of the worst failures, written to `reports/ocr_eval.md` with a timestamp and model identifiers.
- **Test fixture:** include a tiny synthetic eval set (Phase 6 generator) so the harness itself is testable.

**Training (`train/`)**
- `synth_plates.py`: renders Indian plates, standard and BH, one-row and two-row.
  - Use an open-licensed font and document how to swap in a proper plate font.
  - Apply albumentations: motion blur, rain, fog, brightness/contrast, perspective (up to 35°), JPEG artifacts, dirt/occlusion patches, low-light noise.
- `finetune_parseq.py`: fine-tunes PARSeq on synthetic plus real data, with a config file.
- `train_plate_detector.py`: ultralytics training on a user-supplied YOLO dataset, with `data.yaml` provided as a template.
- README section: recommended public datasets for pretraining and fine-tuning, and the rule that local labelled footage is required before trusting accuracy claims.

**GPU**
A `gpu` compose profile uses the CUDA base image. Document Jetson (TensorRT export via ultralytics) and Hailo as deployment notes only.

### Phase 7: Integration tests and docs

**`tests/integration/test_e2e.py`** (runs against the compose stack)
1. Seed the stack and backfill 1 day at high speed.
2. Run 10 simulated minutes containing all scenarios.
3. Assert that:
   - every scenario in `scenarios.json` produced the right alert type for the right plate
   - watchlist exact matches produced zero false negatives
   - a trajectory query for a scenario plate returns ordered sightings matching the simulator's ground truth, with at least 90% of sightings recovered
   - OD responses contain no cell below k
   - an analyst token receives a 403 on `/trajectory`
   - every trajectory call wrote an `audit_log` row
4. Also check that alert end-to-end latency (event ts to alert persisted) has p95 under 3 s at 60× speed. Log the value.

**`README.md`**
- Architecture diagram (Mermaid)
- Quickstart
- Supplying model weights
- Running OCR on your own clips
- Evaluation methodology
- Privacy controls: RBAC, case IDs, audit log, retention TTL, k-anonymity
- What is stubbed and how to replace it
- Scaling notes: Flink or Rust consumers, ClickHouse cluster, edge deployment

## 6. Execution protocol

1. Create `PLAN.md` first. List every phase as a checklist, along with your assumptions.
2. Implement the phases in order. After each phase:
   - run its tests
   - fix any failures
   - update `PLAN.md`
   - then move on
3. Use the terminal to actually run things: `make up`, migrations, tests. Don't assume code works.
4. Prefer small, typed, well-named modules. Put all tunables in config.
5. If an external download fails (OSM, weights), use the documented fallback and continue. Never block the whole build on it.
6. When finished, print a final summary:
   - what works
   - what is stubbed
   - exact commands to run the demo
   - how to add real plate weights

## 7. Definition of Done

- `make up && make seed && make backfill && make test` passes from a clean clone on a CPU machine.
- `make simulate` plus opening the dashboard shows:
  - a live heatmap
  - congested segments
  - alerts arriving
  - a working trajectory playback for a scenario plate
- `make eval` produces `reports/ocr_eval.md` on the bundled synthetic set.
- `ocr-engine run --dry-run` works on a sample video once plate weights are provided.
- There are no fabricated metrics anywhere in code, UI, or docs.
