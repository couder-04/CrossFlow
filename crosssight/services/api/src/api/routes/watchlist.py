"""Watchlist routes (operator/admin only — plate-level data)."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any, cast

from anpr_common.grammar import normalize_plate
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult

from api.db import WatchlistEntry
from api.deps import OperatorUserDep, SessionDep
from api.schemas import WatchlistCreate, WatchlistOut

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


def _entry_out(row: WatchlistEntry) -> WatchlistOut:
    return WatchlistOut(
        plate_norm=row.plate_norm,
        reason=row.reason,
        severity=row.severity,
        added_by=row.added_by,
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


@router.get("", response_model=list[WatchlistOut])
async def list_watchlist(session: SessionDep, _user: OperatorUserDep) -> list[WatchlistOut]:
    result = await session.execute(select(WatchlistEntry).order_by(WatchlistEntry.plate_norm))
    return [_entry_out(r) for r in result.scalars()]


@router.post("", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
async def add_watchlist_entry(
    body: WatchlistCreate,
    session: SessionDep,
    user: OperatorUserDep,
) -> WatchlistOut:
    plate = normalize_plate(body.plate_norm).norm or body.plate_norm.upper().replace(" ", "")
    existing = await session.get(WatchlistEntry, plate)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Plate already on watchlist"
        )
    entry = WatchlistEntry(
        plate_norm=plate,
        reason=body.reason,
        severity=body.severity,
        added_by=user.username,
        expires_at=body.expires_at,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return _entry_out(entry)


@router.delete("/{plate_norm}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_watchlist_entry(
    plate_norm: str,
    session: SessionDep,
    _user: OperatorUserDep,
) -> None:
    plate = normalize_plate(plate_norm).norm or plate_norm.upper().replace(" ", "")
    result = await session.execute(delete(WatchlistEntry).where(WatchlistEntry.plate_norm == plate))
    if cast(CursorResult[Any], result).rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plate not on watchlist")
    await session.commit()


@router.post("/import", response_model=dict)
async def import_watchlist_csv(
    session: SessionDep,
    user: OperatorUserDep,
    file: UploadFile = File(...),
) -> dict:
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    imported = 0
    skipped = 0
    for row in reader:
        raw_plate = row.get("plate_norm") or row.get("plate") or ""
        if not raw_plate.strip():
            skipped += 1
            continue
        plate = normalize_plate(raw_plate).norm or raw_plate.upper().replace(" ", "")
        if await session.get(WatchlistEntry, plate) is not None:
            skipped += 1
            continue
        expires_raw = (row.get("expires_at") or "").strip()
        try:
            expires_at = datetime.fromisoformat(expires_raw) if expires_raw else None
        except ValueError:
            skipped += 1
            continue
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        session.add(
            WatchlistEntry(
                plate_norm=plate,
                reason=row.get("reason") or "imported",
                severity=row.get("severity") or "high",
                added_by=user.username,
                expires_at=expires_at,
            )
        )
        imported += 1
    await session.commit()
    return {"imported": imported, "skipped": skipped}
