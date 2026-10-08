"""Idempotent schema statements for existing databases."""

POSTGRES_STATEMENTS = [
    "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS ops_config JSONB NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS speed_limit_kmh DOUBLE PRECISION",
    "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS notes TEXT",
    "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS priority TEXT",
    """
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'alerts'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%status%'
  LOOP
    EXECUTE format('ALTER TABLE alerts DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$
""",
    """
ALTER TABLE alerts ADD CONSTRAINT alerts_status_check CHECK (
  status IN (
    'new', 'acknowledged', 'dispatched', 'closed', 'false_positive',
    'reviewing', 'approved', 'dismissed'
  )
)
""",
    """
CREATE TABLE IF NOT EXISTS alert_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id UUID NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    actor TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""",
    "CREATE INDEX IF NOT EXISTS idx_alert_reviews_alert ON alert_reviews (alert_id, created_at)",
    """
CREATE TABLE IF NOT EXISTS vehicle_registry (
    plate_norm TEXT PRIMARY KEY,
    vehicle_class TEXT,
    make TEXT,
    model TEXT,
    color TEXT,
    registration_status TEXT NOT NULL DEFAULT 'active',
    owner_ref TEXT,
    source TEXT,
    valid_from TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""",
    """
CREATE TABLE IF NOT EXISTS camera_calibrations (
    camera_id TEXT PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
    homography JSONB,
    coordinate_reference TEXT,
    lanes JSONB,
    speed_calibration JSONB,
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""",
    """
CREATE TABLE IF NOT EXISTS uploads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL,
    filename TEXT NOT NULL,
    object_key TEXT,
    mime TEXT,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    uploaded_by TEXT NOT NULL,
    camera_id TEXT,
    zone_id TEXT,
    captured_at TIMESTAMPTZ,
    preview JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""",
    "CREATE INDEX IF NOT EXISTS idx_uploads_created ON uploads (created_at DESC)",
    """
CREATE TABLE IF NOT EXISTS exports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL,
    format TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    object_key TEXT,
    filename TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ
)
""",
    "CREATE INDEX IF NOT EXISTS idx_exports_created ON exports (created_at DESC)",
]


def clickhouse_statements() -> list[str]:
    return [part.strip() for part in CLICKHOUSE_UPGRADE.split(";") if part.strip()]


CLICKHOUSE_UPGRADE = """
CREATE TABLE IF NOT EXISTS anpr.od_camera_hourly
(
    origin_camera String,
    dest_camera String,
    hour DateTime('UTC'),
    trip_count UInt32
)
ENGINE = SummingMergeTree
ORDER BY (hour, origin_camera, dest_camera);

CREATE TABLE IF NOT EXISTS anpr.dwell_events
(
    plate_norm String,
    camera_id String,
    vehicle_class LowCardinality(String),
    entry_ts DateTime64(3, 'UTC'),
    exit_ts DateTime64(3, 'UTC'),
    dwell_s Float32,
    classification LowCardinality(String),
    confidence Float32,
    crop_key Nullable(String)
)
ENGINE = MergeTree
ORDER BY (camera_id, entry_ts);

CREATE TABLE IF NOT EXISTS anpr.travel_stats_5min
(
    camera_a String,
    camera_b String,
    window_start DateTime('UTC'),
    sample_count UInt32,
    avg_travel_s Float32,
    median_travel_s Float32,
    min_travel_s Float32,
    max_travel_s Float32,
    p90_travel_s Float32,
    avg_speed_kmh Float32,
    distance_m Float32
)
ENGINE = MergeTree
ORDER BY (camera_a, camera_b, window_start);

ALTER TABLE anpr.anpr_reads ADD COLUMN IF NOT EXISTS track_id Nullable(Int32);
ALTER TABLE anpr.anpr_reads ADD COLUMN IF NOT EXISTS bbox Array(Float32);
"""
