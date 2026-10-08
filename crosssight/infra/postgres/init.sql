-- PostGIS init for ANPR platform
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS cameras (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    geom geometry(Point, 4326) NOT NULL,
    heading_deg DOUBLE PRECISION NOT NULL DEFAULT 0,
    lanes INT NOT NULL DEFAULT 2,
    allowed_direction TEXT,
    osm_u BIGINT,
    osm_v BIGINT,
    status TEXT NOT NULL DEFAULT 'active',
    ops_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    speed_limit_kmh DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_cameras_geom ON cameras USING GIST (geom);

CREATE TABLE IF NOT EXISTS zones (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('sensitive', 'restricted', 'ward')),
    geom geometry(Polygon, 4326) NOT NULL,
    active_hours JSONB
);
CREATE INDEX IF NOT EXISTS idx_zones_geom ON zones USING GIST (geom);

CREATE TABLE IF NOT EXISTS watchlist (
    plate_norm TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'high',
    added_by TEXT NOT NULL,
    expires_at TIMESTAMPTZ,
    notes TEXT,
    priority TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type TEXT NOT NULL,
    severity TEXT NOT NULL,
    plate_norm TEXT NOT NULL,
    camera_ids TEXT[] NOT NULL DEFAULT '{}',
    evidence JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN (
            'new', 'acknowledged', 'dispatched', 'closed', 'false_positive',
            'reviewing', 'approved', 'dismissed'
        )),
    needs_verification BOOLEAN NOT NULL DEFAULT FALSE,
    ack_by TEXT,
    dispatched_to TEXT,
    closed_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts (status);
CREATE INDEX IF NOT EXISTS idx_alerts_plate ON alerts (plate_norm);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts (created_at DESC);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'analyst'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    action TEXT NOT NULL,
    plate_norm TEXT,
    case_id TEXT,
    params JSONB NOT NULL DEFAULT '{}',
    ts TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log (ts DESC);

CREATE TABLE IF NOT EXISTS camera_pairs (
    camera_a TEXT NOT NULL REFERENCES cameras(id),
    camera_b TEXT NOT NULL REFERENCES cameras(id),
    distance_m DOUBLE PRECISION NOT NULL,
    adjacent BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (camera_a, camera_b)
);
CREATE INDEX IF NOT EXISTS idx_camera_pairs_adj ON camera_pairs (adjacent) WHERE adjacent;

CREATE TABLE IF NOT EXISTS alert_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id UUID NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    actor TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alert_reviews_alert ON alert_reviews (alert_id, created_at);

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
);

CREATE TABLE IF NOT EXISTS camera_calibrations (
    camera_id TEXT PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
    homography JSONB,
    coordinate_reference TEXT,
    lanes JSONB,
    speed_calibration JSONB,
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
);
CREATE INDEX IF NOT EXISTS idx_uploads_created ON uploads (created_at DESC);

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
);
CREATE INDEX IF NOT EXISTS idx_exports_created ON exports (created_at DESC);
