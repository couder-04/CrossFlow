-- ClickHouse init for ANPR platform
CREATE DATABASE IF NOT EXISTS anpr;

CREATE TABLE IF NOT EXISTS anpr.anpr_reads
(
    event_id UUID,
    camera_id String,
    ts DateTime64(3, 'UTC'),
    plate_raw String,
    plate_norm String,
    plate_valid UInt8,
    plate_format LowCardinality(String),
    confidence Float32,
    char_conf Array(Float32),
    alternates Array(String),
    lane Nullable(Int32),
    direction LowCardinality(Nullable(String)),
    vehicle_class LowCardinality(String),
    color Nullable(String),
    make Nullable(String),
    speed_kmh Nullable(Float32),
    crop_key Nullable(String),
    source LowCardinality(String),
    h3_r8 UInt64,
    h3_r7 UInt64,
    track_id Nullable(Int32),
    bbox Array(Float32),
    INDEX idx_plate_ngram plate_norm TYPE ngrambf_v1(3, 256, 2, 0) GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(ts)
ORDER BY (plate_norm, ts)
TTL toDateTime(ts) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;

-- Projection for camera-time queries
ALTER TABLE anpr.anpr_reads
    ADD PROJECTION IF NOT EXISTS proj_camera_ts
    (
        SELECT *
        ORDER BY (camera_id, ts)
    );

CREATE TABLE IF NOT EXISTS anpr.flow_5min
(
    camera_id String,
    lane Nullable(Int32),
    window_start DateTime('UTC'),
    counts_car UInt32 DEFAULT 0,
    counts_motorcycle UInt32 DEFAULT 0,
    counts_bus UInt32 DEFAULT 0,
    counts_truck UInt32 DEFAULT 0,
    counts_auto UInt32 DEFAULT 0,
    counts_other UInt32 DEFAULT 0,
    avg_speed_kmh Nullable(Float32),
    volume UInt32 DEFAULT 0
)
ENGINE = MergeTree
ORDER BY (camera_id, window_start);

CREATE TABLE IF NOT EXISTS anpr.segment_speed_5min
(
    camera_a String,
    camera_b String,
    window_start DateTime('UTC'),
    median_travel_s Float32,
    median_speed_kmh Float32,
    sample_count UInt32
)
ENGINE = MergeTree
ORDER BY (camera_a, camera_b, window_start);

CREATE TABLE IF NOT EXISTS anpr.od_hourly
(
    origin_h3 UInt64,
    dest_h3 UInt64,
    hour DateTime('UTC'),
    trip_count UInt32
)
ENGINE = SummingMergeTree
ORDER BY (hour, origin_h3, dest_h3);

CREATE TABLE IF NOT EXISTS anpr.heatmap_1min
(
    h3_cell UInt64,
    minute DateTime('UTC'),
    count UInt32
)
ENGINE = SummingMergeTree
ORDER BY (minute, h3_cell);

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
