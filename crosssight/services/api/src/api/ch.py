"""Run the blocking ClickHouse client off the event loop.

``clickhouse_connect`` is synchronous. Calling it directly inside ``async def`` handlers stalls
every other request (and the websockets) for the length of the query, so every call goes
through these helpers, which hand it to Starlette's worker threadpool.
"""

from __future__ import annotations

from typing import Any

from starlette.concurrency import run_in_threadpool


async def ch_query(ch: Any, sql: str, parameters: dict[str, Any] | None = None) -> Any:
    """``ch.query(sql, parameters=...)`` on a worker thread; errors propagate unchanged."""
    if parameters is None:
        return await run_in_threadpool(ch.query, sql)
    return await run_in_threadpool(ch.query, sql, parameters=parameters)


async def ch_command(ch: Any, sql: str, parameters: dict[str, Any] | None = None) -> Any:
    """``ch.command(sql, parameters=...)`` on a worker thread; errors propagate unchanged."""
    if parameters is None:
        return await run_in_threadpool(ch.command, sql)
    return await run_in_threadpool(ch.command, sql, parameters=parameters)
