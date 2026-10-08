"""Application settings loaded from environment."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-demo-jwt-secret-anpr-platform"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    city_query: str | None = Field(default="Pune, India", alias="CITY_QUERY")
    city_bbox: str | None = Field(default=None, alias="CITY_BBOX")
    num_cameras: int = Field(default=60, alias="NUM_CAMERAS")
    num_vehicles: int = Field(default=3000, alias="NUM_VEHICLES")
    max_urban_speed_kmh: float = Field(default=120.0, alias="MAX_URBAN_SPEED_KMH")
    trip_gap_min: int = Field(default=30, alias="TRIP_GAP_MIN")
    od_k_anon: int = Field(default=5, alias="OD_K_ANON")
    raw_retention_days: int = Field(default=30, alias="RAW_RETENTION_DAYS")
    h3_heatmap_res: int = Field(default=8, alias="H3_HEATMAP_RES")
    h3_od_res: int = Field(default=7, alias="H3_OD_RES")
    sim_speed: float = Field(default=60.0, alias="SIM_SPEED")
    sim_publish_frames: bool = Field(default=True, alias="SIM_PUBLISH_FRAMES")
    app_env: Literal["dev", "staging", "prod"] = Field(default="dev", alias="APP_ENV")

    kafka_bootstrap: str = Field(default="localhost:19092", alias="KAFKA_BOOTSTRAP")
    topic_reads: str = Field(default="anpr.reads.v1", alias="TOPIC_READS")
    topic_alerts: str = Field(default="alerts.v1", alias="TOPIC_ALERTS")
    topic_flow: str = Field(default="analytics.flow.v1", alias="TOPIC_FLOW")

    clickhouse_host: str = Field(default="localhost", alias="CLICKHOUSE_HOST")
    clickhouse_port: int = Field(default=8123, alias="CLICKHOUSE_PORT")
    clickhouse_user: str = Field(default="default", alias="CLICKHOUSE_USER")
    clickhouse_password: str = Field(default="", alias="CLICKHOUSE_PASSWORD")
    clickhouse_db: str = Field(default="anpr", alias="CLICKHOUSE_DB")

    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5433, alias="POSTGRES_PORT")
    postgres_user: str = Field(default="anpr", alias="POSTGRES_USER")
    postgres_password: str = Field(default="anpr", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="anpr", alias="POSTGRES_DB")

    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    minio_endpoint: str = Field(default="localhost:9000", alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="minioadmin", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="minioadmin", alias="MINIO_SECRET_KEY")
    minio_bucket: str = Field(default="anpr-crops", alias="MINIO_BUCKET")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")
    minio_public_endpoint: str = Field(
        default="",
        alias="MINIO_PUBLIC_ENDPOINT",
        description=(
            "host:port browsers use to reach MinIO. Presigned URLs (camera wall, crops, alert "
            "evidence) are signed for this host; the host is part of the signature, so they "
            "can't be rewritten later. Empty = MINIO_ENDPOINT (fine outside Docker)."
        ),
    )
    minio_region: str = Field(default="us-east-1", alias="MINIO_REGION")

    jwt_secret: str = Field(default=DEFAULT_JWT_SECRET, alias="JWT_SECRET")
    jwt_expire_minutes: int = Field(default=480, alias="JWT_EXPIRE_MINUTES")
    seed_admin_user: str = Field(default="admin", alias="SEED_ADMIN_USER")
    seed_admin_pass: str = Field(default="admin123", alias="SEED_ADMIN_PASS")
    seed_operator_user: str = Field(default="operator", alias="SEED_OPERATOR_USER")
    seed_operator_pass: str = Field(default="operator123", alias="SEED_OPERATOR_PASS")
    seed_analyst_user: str = Field(default="analyst", alias="SEED_ANALYST_USER")
    seed_analyst_pass: str = Field(default="analyst123", alias="SEED_ANALYST_PASS")

    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    cors_origins: str = Field(default="http://localhost:3000", alias="CORS_ORIGINS")

    ocr_backend: Literal["plateocr", "legacy"] = Field(default="plateocr", alias="OCR_BACKEND")
    plate_det_weights: str = Field(default="", alias="PLATE_DET_WEIGHTS")
    parseq_weights: str = Field(default="", alias="PARSEEQ_WEIGHTS")
    ocr_camera_id: str = Field(default="cam-demo", alias="OCR_CAMERA_ID")
    plateocr_detector: str = Field(
        default="yolo-v9-s-608-license-plate-end2end",
        alias="PLATEOCR_DETECTOR",
    )
    plateocr_ocr_model: str = Field(
        default="india-v1.1",
        alias="PLATEOCR_OCR_MODEL",
    )
    plateocr_device: str = Field(default="auto", alias="PLATEOCR_DEVICE")
    plateocr_det_conf: float = Field(default=0.4, alias="PLATEOCR_DET_CONF")
    plateocr_min_ocr_conf: float = Field(
        default=0.5,
        alias="PLATEOCR_MIN_OCR_CONF",
        description=(
            "Drop per-frame plate reads below this mean character confidence. Garbage reads "
            "(no plate, whole photo OCR'd) score ~0.05-0.2; clear plates score 0.9+."
        ),
    )
    plateocr_ocr_config: str = Field(default="", alias="PLATEOCR_OCR_CONFIG")
    plateocr_plate_format: str = Field(
        default="india",
        alias="PLATEOCR_PLATE_FORMAT",
        description="india | none | empty (auto from model). India fine-tune defaults to india.",
    )
    plateocr_tta: bool = Field(
        default=False,
        alias="PLATEOCR_TTA",
        description=(
            "Average OCR slot probs over CLAHE/pad/scale views before India decode. ~7x OCR "
            "cost for +1 pt on in_crops (within noise); off for real-time video."
        ),
    )
    plateocr_bbox_pad: bool = Field(
        default=False,
        alias="PLATEOCR_BBOX_PAD",
        description="Try padded/shrunk detector crops and keep the best OCR read (~4x OCR cost).",
    )
    ocr_frame_stride: int = Field(
        default=1,
        ge=1,
        alias="OCR_FRAME_STRIDE",
        description="Process every Nth video frame (use 2-3 on CPU-only hosts).",
    )
    watchlist_min_conf: float = Field(
        default=0.5,
        alias="WATCHLIST_MIN_CONF",
        description="Reads below this fused confidence never raise watchlist alerts.",
    )
    registry_path: str = Field(default="", alias="REGISTRY_PATH")
    route_anomaly_min_cameras: int = Field(default=3, alias="ROUTE_ANOMALY_MIN_CAMERAS")
    route_anomaly_distance_m: float = Field(default=8000.0, alias="ROUTE_ANOMALY_DISTANCE_M")

    stopped_short_s: float = Field(default=45.0, alias="STOPPED_SHORT_S")
    stopped_excessive_s: float = Field(default=120.0, alias="STOPPED_EXCESSIVE_S")
    stopped_incident_s: float = Field(default=180.0, alias="STOPPED_INCIDENT_S")
    stopped_gap_s: float = Field(default=90.0, alias="STOPPED_GAP_S")

    health_stale_s: float = Field(default=300.0, alias="HEALTH_STALE_S")
    health_offline_s: float = Field(default=900.0, alias="HEALTH_OFFLINE_S")
    health_expected_rpm: float = Field(default=1.0, alias="HEALTH_EXPECTED_RPM")
    health_rate_low_ratio: float = Field(default=0.25, alias="HEALTH_RATE_LOW_RATIO")
    health_rate_high_ratio: float = Field(default=4.0, alias="HEALTH_RATE_HIGH_RATIO")
    health_window_s: float = Field(default=300.0, alias="HEALTH_WINDOW_S")

    publish_annotated_frames: bool = Field(default=True, alias="PUBLISH_ANNOTATED_FRAMES")

    stale_job_minutes: int = Field(default=30, alias="STALE_JOB_MINUTES")
    max_upload_bytes: int = Field(default=512 * 1024 * 1024, alias="MAX_UPLOAD_BYTES")
    max_upload_video_bytes: int = Field(default=512 * 1024 * 1024, alias="MAX_UPLOAD_VIDEO_BYTES")
    max_upload_image_bytes: int = Field(default=15 * 1024 * 1024, alias="MAX_UPLOAD_IMAGE_BYTES")
    max_upload_table_bytes: int = Field(default=10 * 1024 * 1024, alias="MAX_UPLOAD_TABLE_BYTES")
    export_url_ttl_s: int = Field(default=900, alias="EXPORT_URL_TTL_S")
    export_sync_row_limit: int = Field(default=5000, alias="EXPORT_SYNC_ROW_LIMIT")

    map_style_url: str = Field(
        default="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        alias="NEXT_PUBLIC_MAP_STYLE_URL",
    )

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_dsn_sync(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
