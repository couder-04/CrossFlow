"""Lightweight /healthz HTTP endpoint for workers."""

from __future__ import annotations

import logging
from collections.abc import Callable

from aiohttp import web

logger = logging.getLogger(__name__)


class HealthServer:
    def __init__(
        self,
        name: str,
        port: int,
        ready_check: Callable[[], bool] | None = None,
    ) -> None:
        self.name = name
        self.port = port
        self._ready_check = ready_check
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._healthy = True

    def set_healthy(self, healthy: bool) -> None:
        self._healthy = healthy

    async def _handle_healthz(self, _request: web.Request) -> web.Response:
        if not self._healthy:
            return web.json_response({"status": "shutting_down", "worker": self.name}, status=503)
        if self._ready_check is not None and not self._ready_check():
            return web.json_response({"status": "not_ready", "worker": self.name}, status=503)
        return web.json_response({"status": "ok", "worker": self.name})

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/healthz", self._handle_healthz)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="0.0.0.0", port=self.port)
        await self._site.start()
        logger.info("Health server for %s listening on :%d/healthz", self.name, self.port)

    async def stop(self) -> None:
        self._healthy = False
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
