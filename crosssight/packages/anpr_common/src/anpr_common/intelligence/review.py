"""Human review transitions. Automatic enforcement is intentionally absent."""

from __future__ import annotations

_ALLOWED: dict[str, set[str]] = {
    "new": {"reviewing", "approved", "dismissed", "closed", "acknowledged"},
    "acknowledged": {"reviewing", "approved", "dismissed", "closed", "dispatched"},
    "dispatched": {"reviewing", "approved", "closed"},
    "reviewing": {"approved", "dismissed", "closed"},
    "approved": {"closed"},
    "dismissed": {"closed"},
    "false_positive": {"closed", "dismissed"},
    "closed": set(),
}


class ReviewError(ValueError):
    pass


def transition_status(current: str, target: str, note: str | None) -> str:
    current = current.lower()
    target = target.lower()
    allowed = _ALLOWED.get(current)
    if allowed is None:
        raise ReviewError(f"unknown status {current}")
    if target not in allowed:
        raise ReviewError(f"cannot move {current} to {target}")
    if target in {"closed", "dismissed", "approved"} and not (note and note.strip()):
        raise ReviewError(f"{target} requires a note")
    return target
