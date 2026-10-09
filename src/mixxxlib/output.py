"""JSON envelope, error payloads and plain-text rendering (spec §4, §5)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from mixxxlib.errors import MixxxlibError
from mixxxlib.model import (
    BackupEntry, Crate, CrateNode, Cue, Finding, MusicalKey, MutationResult, Track, WarningMessage, format_color,
)


def as_json(obj: Any) -> Any:
    if isinstance(obj, MusicalKey):
        return obj.to_json()
    if isinstance(obj, Cue):
        return {"id": obj.id, "track_id": obj.track_id, "type": obj.type, "type_id": obj.type_id, "slot": obj.slot,
                "position_samples": obj.position_samples, "position_seconds": obj.position_seconds,
                "length_samples": obj.length_samples, "length_seconds": obj.length_seconds,
                "label": obj.label, "color": obj.color, "color_hex": format_color(obj.color)}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: as_json(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): as_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [as_json(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return [as_json(v) for v in sorted(obj)]
    if isinstance(obj, Path):
        return str(obj)
    return obj


def mutation_block(result: MutationResult) -> dict[str, Any]:
    operations = list(result.statements) + list(result.file_operations)
    return {"dry_run": result.dry_run, "backup": result.backup, "integrity": result.integrity,
            "statements": operations if result.dry_run else len(operations)}


def envelope(command: str, data: Any, warnings: list[WarningMessage] | tuple[WarningMessage, ...],
             mutation: MutationResult | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True, "command": command, "data": as_json(data), "warnings": as_json(list(warnings))}
    if mutation is not None:
        payload["mutation"] = mutation_block(mutation)
    return payload


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def error_text(err: MixxxlibError) -> str:
    return f"mixxxlib: {err.code}: {err.message}"


# ---------------------------------------------------------------- plain text

def cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    text_rows = [[cell(row.get(col)) for col in columns] for row in rows]
    widths = [max(len(col), *(len(r[i]) for r in text_rows)) if text_rows else len(col) for i, col in enumerate(columns)]
    lines = ["  ".join(col.ljust(widths[i]) for i, col in enumerate(columns)).rstrip()]
    for row in text_rows:
        lines.append("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)).rstrip())
    return "\n".join(lines)


def kv(mapping: dict[str, Any], indent: int = 0) -> str:
    lines = []
    pad = "  " * indent
    for key, value in mapping.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.append(kv(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{pad}{key}: {', '.join(cell(v) if not isinstance(v, dict) else dumps(v) for v in value)}")
        else:
            lines.append(f"{pad}{key}: {cell(value)}")
    return "\n".join(lines)


def render_crates(crates: list[Crate]) -> str:
    rows = [{**as_json(c), "tracks": c.track_count} for c in crates]
    return table(rows, ["id", "name", "parent_id", "tracks", "locked", "show", "autodj_source"])


def render_crate(crate: Crate) -> str:
    return kv(as_json(crate))


def render_tree(nodes: list[CrateNode], depth: int = 0) -> str:
    lines = []
    for node in nodes:
        lines.append(f"{'  ' * depth}{node.crate.name} (id {node.crate.id}, {node.crate.track_count} tracks)")
        if node.children:
            lines.append(render_tree(list(node.children), depth + 1))
    return "\n".join(lines)


def render_tracks(tracks: list[Track]) -> str:
    rows = [{"id": t.id, "artist": t.metadata.artist, "title": t.metadata.title, "album": t.metadata.album,
             "bpm": t.metadata.bpm, "key": t.metadata.key.text, "duration": t.audio.duration} for t in tracks]
    return table(rows, ["id", "artist", "title", "album", "bpm", "key", "duration"])


def render_track(track: Track) -> str:
    return kv(as_json(track))


def render_cues(cues: list[Cue]) -> str:
    rows = [{"id": c.id, "type": c.type, "slot": c.slot if c.slot >= 0 else None, "position": c.position_seconds,
             "length": c.length_seconds, "label": c.label, "color": format_color(c.color)} for c in cues]
    return table(rows, ["id", "type", "slot", "position", "length", "label", "color"])


def render_findings(findings: list[Finding]) -> str:
    return "\n".join(f"[{f.severity}] {f.check}: {f.message}" for f in findings)


def render_backups(entries: list[BackupEntry]) -> str:
    rows = [{"name": e.name, "kind": e.kind, "size": e.size, "modified": e.modified, "age_hours": round(e.age_seconds / 3600, 1)} for e in entries]
    return table(rows, ["name", "kind", "size", "modified", "age_hours"])


def render_mutation(result: MutationResult) -> str:
    operations = list(result.statements) + list(result.file_operations)
    if result.dry_run:
        return "\n".join(["dry run; nothing was changed. Would run:"] + [f"  {op}" for op in operations])
    return kv({"backup": result.backup, "integrity": result.integrity, "statements": len(operations)})
