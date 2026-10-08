#!/usr/bin/env python3
"""Export JSON Schema and generate TypeScript types for the dashboard."""

from __future__ import annotations

import json
from pathlib import Path

from anpr_common.schemas import export_json_schemas

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "packages" / "anpr_common" / "schemas"
TS_DIR = ROOT / "apps" / "dashboard" / "src" / "types"


def _json_type_to_ts(schema: dict, defs: dict, name: str | None = None) -> str:
    if "$ref" in schema:
        ref = schema["$ref"].split("/")[-1]
        return ref
    if "anyOf" in schema:
        parts = [_json_type_to_ts(s, defs) for s in schema["anyOf"]]
        # filter null
        non_null = [p for p in parts if p != "null"]
        if "null" in parts and non_null:
            return f"{' | '.join(non_null)} | null"
        return " | ".join(parts)
    t = schema.get("type")
    if isinstance(t, list):
        return " | ".join(
            "null" if x == "null" else _json_type_to_ts({**schema, "type": x}, defs) for x in t
        )
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])
    if t == "string":
        return "string"
    if t == "integer" or t == "number":
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "array":
        items = schema.get("items", {})
        return f"Array<{_json_type_to_ts(items, defs)}>"
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        lines = []
        for k, v in props.items():
            opt = "" if k in required else "?"
            lines.append(f"  {k}{opt}: {_json_type_to_ts(v, defs)};")
        body = "\n".join(lines)
        return "{\n" + body + "\n}" if name is None else body
    if t == "null":
        return "null"
    return "unknown"


def schema_to_interface(name: str, schema: dict) -> str:
    defs = schema.get("$defs", {})
    parts = []
    for dname, dschema in defs.items():
        if dschema.get("enum"):
            parts.append(f"export type {dname} = {_json_type_to_ts(dschema, defs)};\n")
        else:
            body = _json_type_to_ts(dschema, defs, name=dname)
            if body.startswith("{"):
                parts.append(f"export interface {dname} {body}\n")
            else:
                parts.append(f"export type {dname} = {body};\n")
    body = _json_type_to_ts(schema, defs, name=name)
    if "properties" in schema:
        prop_body = _json_type_to_ts(schema, defs, name=name)
        # When name is set, _json_type_to_ts returns only property lines
        if not prop_body.strip().startswith("{"):
            parts.append(f"export interface {name} {{\n{prop_body}\n}}\n")
        else:
            parts.append(f"export interface {name} {prop_body}\n")
    return "\n".join(parts)


def main() -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    TS_DIR.mkdir(parents=True, exist_ok=True)
    paths = export_json_schemas(SCHEMA_DIR)
    for path in paths:
        schema = json.loads(path.read_text())
        name = path.stem
        ts = schema_to_interface(name, schema)
        out = TS_DIR / f"{name}.ts"
        out.write_text(f"/* auto-generated from {path.name} — do not edit */\n\n{ts}\n")
        print(f"wrote {out}")
    # Do not overwrite index.ts — it re-exports generated + dashboard domain types.
    index = TS_DIR / "index.ts"
    if not index.exists():
        exports = "\n".join(f"export * from './{p.stem}';" for p in paths)
        index.write_text(exports + "\n")
    print("done")


if __name__ == "__main__":
    main()
