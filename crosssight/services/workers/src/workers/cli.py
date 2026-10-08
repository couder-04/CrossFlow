"""CLI entrypoint for ANPR workers."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from workers.alerts.engine import AlertsWorker
from workers.analytics import AnalyticsWorker
from workers.ingest import IngestWorker

logger = logging.getLogger(__name__)

WORKERS = ("ingest", "analytics", "alerts", "all")


async def _run_one(name: str) -> None:
    if name == "ingest":
        await IngestWorker().run()
    elif name == "analytics":
        await AnalyticsWorker().run()
    elif name == "alerts":
        await AlertsWorker().run()
    else:
        raise ValueError(f"Unknown worker: {name}")


async def _run_all() -> None:
    async def _guard(name: str, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker %s exited with error", name)
            raise

    await asyncio.gather(
        _guard("ingest", IngestWorker().run()),
        _guard("analytics", AnalyticsWorker().run()),
        _guard("alerts", AlertsWorker().run()),
    )


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workers", description="ANPR stream workers")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="Start one or all workers")
    run_parser.add_argument(
        "--worker",
        choices=WORKERS,
        default="all",
        help="Worker to run (default: all)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    if args.command == "run":
        if args.worker == "all":
            asyncio.run(_run_all())
        else:
            asyncio.run(_run_one(args.worker))
        return 0
    return 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
