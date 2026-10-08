"""Pipeline events print under dry-run and publish when Kafka is connected."""

import json
from types import SimpleNamespace

from ocr_engine.pipeline import emit


def test_emit_dry_run_prints_indented_json(capsys) -> None:
    sent: list[dict] = []
    emit(
        {"plate": "MH12AB1234"},
        dry_run=True,
        producer=SimpleNamespace(send=lambda **kwargs: sent.append(kwargs)),
    )
    out = capsys.readouterr().out
    assert json.loads(out) == {"plate": "MH12AB1234"}
    assert "\n" in out
    assert sent == []


def test_emit_publishes_when_not_dry_run() -> None:
    sent: list[dict] = []

    class _Producer:
        def send(self, topic: str, key: str | None = None, value: dict | None = None) -> None:
            sent.append({"topic": topic, "key": key, "value": value})

    emit(
        {"plate": "MH12AB1234"},
        dry_run=False,
        producer=_Producer(),
        topic="anpr.reads.v1",
        key="MH12AB1234",
    )
    assert sent == [
        {"topic": "anpr.reads.v1", "key": "MH12AB1234", "value": {"plate": "MH12AB1234"}}
    ]
