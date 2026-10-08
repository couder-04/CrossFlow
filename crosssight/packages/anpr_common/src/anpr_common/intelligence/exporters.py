"""CSV, JSON, and a small real PDF writer. No dashboard HTML is exported."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime


def export_filename(kind: str, fmt: str, when: datetime, export_id: str) -> str:
    stamp = when.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_kind = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in kind)[:40]
    suffix = {"csv": "csv", "pdf": "pdf", "json": "json", "mp4": "mp4"}.get(fmt, fmt)
    return f"crosssight_{safe_kind}_{stamp}_{export_id[:8]}.{suffix}"


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    if text[:1] in {"=", "+", "-", "@"}:
        return "'" + text
    return text


def rows_to_csv(headers: list[str], rows: list[list[object]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_csv_cell(value) for value in row])
    return buf.getvalue().encode("utf-8")


def rows_to_json(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=str, indent=2).encode("utf-8")


def _pdf_escape(text: str) -> str:
    cleaned = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in text)
    return cleaned.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def rows_to_pdf(title: str, meta: list[str], sections: list[tuple[str, list[str]]]) -> bytes:
    """Build a multi-page text PDF with CrossSight branding and the supplied sections."""
    lines = ["CrossSight", title, ""]
    lines.extend(meta)
    lines.append("")
    for heading, body in sections:
        lines.append(heading)
        lines.extend(body or ["-"])
        lines.append("")
    page_size = 46
    pages = [
        lines[offset : offset + page_size] for offset in range(0, max(len(lines), 1), page_size)
    ]
    objects: list[bytes] = []

    def add(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    content_ids: list[int] = []
    for page_lines in pages:
        commands = ["BT", "/F1 11 Tf", "50 800 Td", "14 TL"]
        for line in page_lines:
            commands.append(f"({_pdf_escape(line)}) '")
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1", errors="replace")
        content_ids.append(
            add(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
        )
    page_ids: list[int] = []
    for content_num in content_ids:
        page_ids.append(
            add(
                (
                    "<< /Type /Page /Parent PAGES 0 R /MediaBox [0 0 595 842] "
                    f"/Contents {content_num} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
                ).encode()
            )
        )
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    pages_id = add(f"<< /Type /Pages /Count {len(page_ids)} /Kids [{kids}] >>".encode())
    for pid in page_ids:
        objects[pid - 1] = objects[pid - 1].replace(
            b"/Parent PAGES 0 R", f"/Parent {pages_id} 0 R".encode()
        )
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode())
        out.write(obj)
        out.write(b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer << /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n".encode())
    out.write(f"startxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def clip_window(
    event_offset_s: float, before_s: float, after_s: float, duration_s: float
) -> tuple[float, float] | None:
    if before_s < 0 or after_s < 0 or duration_s <= 0:
        return None
    start = max(0.0, event_offset_s - before_s)
    end = min(duration_s, event_offset_s + after_s)
    if end <= start:
        return None
    return start, end


def overlay_plan(metadata: dict) -> list[dict]:
    """Draw instructions from existing inference metadata. Does not run a model."""
    plan = []
    bbox = metadata.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        plan.append({"op": "rect", "bbox": [float(v) for v in bbox]})
    if metadata.get("track_id") is not None:
        plan.append({"op": "label", "text": f"id {metadata['track_id']}"})
    if metadata.get("plate"):
        conf = metadata.get("confidence")
        text = str(metadata["plate"])
        if conf is not None:
            text = f"{text} {float(conf):.2f}"
        plan.append({"op": "label", "text": text})
    if metadata.get("vehicle_class"):
        plan.append({"op": "label", "text": str(metadata["vehicle_class"])})
    if metadata.get("alert"):
        plan.append({"op": "marker", "text": str(metadata["alert"])})
    return plan
