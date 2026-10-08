"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from anpr_common.config import (
    DEFAULT_JWT_SECRET,
    DEFAULT_SEED_PASSWORDS,
    Settings,
    get_settings,
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.clickhouse_retention import apply_clickhouse_retention_from_settings
from api.db import get_session_factory
from api.jobs import apply_stale_job_recovery
from api.migrate import apply_postgres_upgrade
from api.routes import (
    alerts,
    analytics_flow,
    analytics_heatmap,
    analytics_od,
    audit,
    auth,
    cameras,
    crops,
    evidence,
    exports,
    imports,
    platform,
    signals,
    sources,
    trajectory,
    uploads,
    watchlist,
    ws,
    zones,
)
from api.seed import ensure_seed_users

logger = logging.getLogger(__name__)


def _validate_settings(settings: Settings) -> None:
    """Refuse default demo secrets outside local development."""
    if settings.app_env == "dev":
        logger.warning(
            "APP_ENV=dev: demo secrets and seed passwords are accepted. "
            "Set APP_ENV=prod (or staging) and rotate secrets before exposing this API."
        )
        return
    unsafe: list[str] = []
    if settings.jwt_secret == DEFAULT_JWT_SECRET:
        unsafe.append("JWT_SECRET")
    if settings.postgres_password == "anpr":
        unsafe.append("POSTGRES_PASSWORD")
    if settings.minio_secret_key == "minioadmin":
        unsafe.append("MINIO_SECRET_KEY")
    for env_name, value in (
        ("SEED_ADMIN_PASS", settings.seed_admin_pass),
        ("SEED_OPERATOR_PASS", settings.seed_operator_pass),
        ("SEED_ANALYST_PASS", settings.seed_analyst_pass),
    ):
        if value in DEFAULT_SEED_PASSWORDS:
            unsafe.append(env_name)
    if unsafe:
        raise RuntimeError(
            "Refusing to boot with default "
            + ", ".join(unsafe)
            + f" while APP_ENV={settings.app_env}"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _validate_settings(settings)
    factory = get_session_factory(settings)
    async with factory() as session:
        await apply_postgres_upgrade(session)
        await apply_stale_job_recovery(session, stale_minutes=settings.stale_job_minutes)
        await ensure_seed_users(session, settings)
    try:
        apply_clickhouse_retention_from_settings(settings)
    except Exception:
        logger.warning("ClickHouse retention TTL was not applied", exc_info=True)
    logger.info("API started; seed users ensured")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ANPR Platform API",
        version="0.1.0",
        description="City-wide ANPR intelligence REST and WebSocket API",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    app.include_router(auth.router)
    app.include_router(cameras.router)
    app.include_router(zones.router)
    app.include_router(watchlist.router)
    app.include_router(trajectory.router)
    app.include_router(analytics_heatmap.router)
    app.include_router(analytics_flow.router)
    app.include_router(analytics_od.router)
    app.include_router(alerts.router)
    app.include_router(platform.router)
    app.include_router(signals.router)
    app.include_router(imports.router)
    app.include_router(uploads.router)
    app.include_router(exports.router)
    app.include_router(evidence.router)
    app.include_router(sources.router)
    app.include_router(crops.router)
    app.include_router(audit.router)
    app.include_router(ws.router)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
