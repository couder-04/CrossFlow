"""Export jobs call analytics routes directly, so Query defaults must stay ints."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from api.auth import Role, UserContext
from api.routes.platform import dwell, investigation


class _Scalars:
    def scalars(self):
        return []


class _Session:
    def add(self, _row) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def execute(self, *_args, **_kwargs):
        return _Scalars()


class _ClickHouse:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def query(self, sql: str, parameters: dict | None = None):
        self.calls.append({"sql": sql, "parameters": dict(parameters or {})})
        return SimpleNamespace(result_rows=[])


def _actor() -> UserContext:
    return UserContext(id="", username="export", role=Role.admin)


async def test_investigation_direct_call_sends_int_limit() -> None:
    ch = _ClickHouse()
    start = datetime(2026, 9, 30, tzinfo=UTC)
    end = datetime(2026, 9, 30, 1, tzinfo=UTC)
    await investigation(ch, _Session(), _actor(), "MH01AB1234", None, start, end)  # type: ignore[arg-type]
    limit = ch.calls[0]["parameters"]["limit"]
    assert isinstance(limit, int)
    assert limit == 500


async def test_dwell_direct_call_sends_int_limit() -> None:
    ch = _ClickHouse()
    settings = SimpleNamespace(
        stopped_short_s=30,
        stopped_excessive_s=120,
        stopped_incident_s=180,
        stopped_gap_s=90,
    )
    await dwell(ch, settings, _actor(), None, None, None)  # type: ignore[arg-type]
    limit = ch.calls[0]["parameters"]["limit"]
    assert isinstance(limit, int)
    assert limit == 500


async def test_investigation_export_uses_role_and_row_limit() -> None:
    from api._transfer_helpers import _collect

    ch = _ClickHouse()
    settings = SimpleNamespace(export_sync_row_limit=5000)
    headers, records, _sections = await _collect(
        _Session(),
        ch,
        settings,
        "investigation",
        {"plate": "MH01AB1234"},
        "admin",
    )
    assert headers[0] == "camera_id"
    assert records == []
    limit = ch.calls[0]["parameters"]["limit"]
    assert isinstance(limit, int)
    assert limit == 5000


async def test_sightings_export_queries_reads_without_a_plate() -> None:
    from api._transfer_helpers import _collect

    ch = _ClickHouse()
    settings = SimpleNamespace(export_sync_row_limit=5000)
    headers, records, _sections = await _collect(
        _Session(),
        ch,
        settings,
        "sightings",
        {},
        "admin",
    )
    assert headers == ["camera_id", "ts", "plate_norm", "confidence", "vehicle_class"]
    assert records == []
    limit = ch.calls[0]["parameters"]["limit"]
    assert isinstance(limit, int)
    assert limit == 5000
    assert "anpr_reads" in ch.calls[0]["sql"]


def test_read_stored_releases_the_object() -> None:
    from api._transfer_helpers import read_stored

    class _Obj:
        def __init__(self) -> None:
            self.closed = False
            self.released = False

        def read(self) -> bytes:
            return b"plate,count\n"

        def close(self) -> None:
            self.closed = True

        def release_conn(self) -> None:
            self.released = True

    class _Minio:
        def __init__(self) -> None:
            self.obj = _Obj()

        def get_object(self, bucket: str, key: str) -> _Obj:
            assert bucket == "anpr"
            assert key == "exports/1/file.csv"
            return self.obj

    client = _Minio()
    payload = read_stored(client, SimpleNamespace(minio_bucket="anpr"), "exports/1/file.csv")
    assert payload == b"plate,count\n"
    assert client.obj.closed
    assert client.obj.released
