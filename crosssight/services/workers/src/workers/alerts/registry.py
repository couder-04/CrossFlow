"""Vehicle registry client interface (Vahan integration + local file demo)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from anpr_common.schemas import VehicleClass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegistryRecord:
    plate_norm: str
    vehicle_class: VehicleClass | None = None
    color: str | None = None
    make: str | None = None
    status: str = "unknown"


class RegistryClient(Protocol):
    """Lookup registered vehicle attributes for a plate.

    A production deployment would call the Vahan / state RTO API here.
    """

    async def lookup(self, plate_norm: str) -> RegistryRecord: ...


class NoopRegistryClient:
    """Default no-op implementation — always returns unknown."""

    async def lookup(self, plate_norm: str) -> RegistryRecord:
        return RegistryRecord(plate_norm=plate_norm.upper(), status="unknown")


class FileRegistryClient:
    """Local JSON/CSV registry for demos without Vahan.

    JSON format: ``{"MH12AB1234": {"vehicle_class": "truck", "color": "white", "make": "Tata"}}``
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._records: dict[str, RegistryRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            logger.warning("Registry file not found: %s", self.path)
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Registry file must be a JSON object: {self.path}")  # noqa: TRY004
        for plate, meta in raw.items():
            if not isinstance(meta, dict):
                continue
            vc_raw = meta.get("vehicle_class")
            vc: VehicleClass | None = None
            if vc_raw:
                try:
                    vc = VehicleClass(str(vc_raw).lower())
                except ValueError:
                    vc = VehicleClass.other
            self._records[str(plate).upper().replace(" ", "")] = RegistryRecord(
                plate_norm=str(plate).upper().replace(" ", ""),
                vehicle_class=vc,
                color=meta.get("color"),
                make=meta.get("make"),
                status="found",
            )
        logger.info("Loaded %d registry records from %s", len(self._records), self.path)

    async def lookup(self, plate_norm: str) -> RegistryRecord:
        key = plate_norm.upper().replace(" ", "")
        return self._records.get(key, RegistryRecord(plate_norm=key, status="unknown"))


def build_registry_client(path: str | None = None) -> RegistryClient:
    """Return FileRegistryClient when path is set, else NoopRegistryClient."""
    if path and path.strip():
        return FileRegistryClient(path.strip())
    return NoopRegistryClient()
