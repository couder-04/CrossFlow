"""WebSocket live feed from Redis pub/sub."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from anpr_common.config import Settings
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError

from api.auth import decode_access_token
from api.deps import get_redis, get_settings_dep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

ALLOWED_CHANNELS = frozenset({"heatmap", "alerts", "flow", "reads"})
WALL_CHANNELS = ("frames", "reads")
AUTH_COOKIE = "anpr_token"


class _LeakyBucket:
    """Allow at most `rate` events per second."""

    def __init__(self, rate: float = 20.0) -> None:
        self.rate = rate
        self.tokens = rate
        self.updated = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.updated
        self.updated = now
        self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


def _ws_token(websocket: WebSocket) -> str | None:
    auth = websocket.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    cookie = websocket.cookies.get(AUTH_COOKIE)
    if cookie:
        return cookie
    token = websocket.query_params.get("token")
    return token or None


async def _accept_authenticated(websocket: WebSocket) -> bool:
    await websocket.accept()
    token = _ws_token(websocket)
    if not token:
        await websocket.close(code=1008)
        return False
    try:
        decode_access_token(token)
    except JWTError:
        await websocket.close(code=1008)
        return False
    return True


def _decode_pubsub(message: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if message.get("type") != "message":
        return None
    channel = message.get("channel")
    data = message.get("data")
    if isinstance(channel, bytes):
        channel = channel.decode("utf-8")
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    try:
        payload = json.loads(data) if isinstance(data, str) else {"raw": data}
    except (json.JSONDecodeError, TypeError):
        payload = {"raw": data}
    if not isinstance(payload, dict):
        payload = {"raw": payload}
    return str(channel), payload


async def _stream_channels(
    websocket: WebSocket,
    settings: Settings,
    channels: tuple[str, ...],
    *,
    camera_id: str | None = None,
    bucket: _LeakyBucket | None = None,
) -> None:
    redis = await get_redis(settings)
    pubsub = redis.pubsub()
    await pubsub.subscribe(*channels)

    async def reader() -> None:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message:
                decoded = _decode_pubsub(message)
                if decoded is not None:
                    channel, payload = decoded
                    matched = camera_id is None or payload.get("camera_id") == camera_id
                    if matched and (bucket is None or bucket.allow()):
                        await websocket.send_json({"channel": channel, "data": payload})
            await asyncio.sleep(0.01)

    reader_task = asyncio.create_task(reader())
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass
        await pubsub.unsubscribe(*channels)
        await pubsub.aclose()


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    await websocket.accept()
    settings = get_settings_dep()
    redis = await get_redis(settings)
    pubsub = redis.pubsub()
    subscribed: set[str] = set()

    async def reader() -> None:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                channel = message.get("channel")
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                payload: dict[str, Any]
                try:
                    payload = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    payload = {"raw": data}
                await websocket.send_json({"channel": channel, "data": payload})
            await asyncio.sleep(0.01)

    reader_task: asyncio.Task | None = None
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "invalid_json"})
                continue

            if "subscribe" in msg:
                channels = msg["subscribe"]
                if not isinstance(channels, list):
                    await websocket.send_json({"error": "subscribe must be a list"})
                    continue
                for ch in channels:
                    if ch not in ALLOWED_CHANNELS:
                        await websocket.send_json({"error": f"unknown channel: {ch}"})
                        continue
                    if ch not in subscribed:
                        await pubsub.subscribe(ch)
                        subscribed.add(ch)
                if reader_task is None and subscribed:
                    reader_task = asyncio.create_task(reader())
                await websocket.send_json({"subscribed": sorted(subscribed)})

            elif "unsubscribe" in msg:
                for ch in msg["unsubscribe"]:
                    if ch in subscribed:
                        await pubsub.unsubscribe(ch)
                        subscribed.discard(ch)
                await websocket.send_json({"subscribed": sorted(subscribed)})

            elif msg.get("ping"):
                await websocket.send_json({"pong": True})

    except WebSocketDisconnect:
        pass
    finally:
        if reader_task is not None:
            reader_task.cancel()
        if subscribed:
            await pubsub.unsubscribe(*subscribed)
        await pubsub.aclose()


@router.websocket("/ws/cameras/{camera_id}")
async def ws_camera(websocket: WebSocket, camera_id: str) -> None:
    if not await _accept_authenticated(websocket):
        return
    settings = get_settings_dep()
    await _stream_channels(websocket, settings, WALL_CHANNELS, camera_id=camera_id)


@router.websocket("/ws/wall")
async def ws_wall(websocket: WebSocket) -> None:
    if not await _accept_authenticated(websocket):
        return
    settings = get_settings_dep()
    await _stream_channels(
        websocket,
        settings,
        WALL_CHANNELS,
        bucket=_LeakyBucket(rate=20.0),
    )
