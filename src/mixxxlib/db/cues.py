"""All SQL for cues plus slot/position validation (spec §6.3, §8.4)."""

from __future__ import annotations

import sqlite3
from typing import Any

from mixxxlib.db.connection import MutationPlan, Statement
from mixxxlib.errors import NotFoundError, ValidationError
from mixxxlib.model import CUE_TYPE_IDS, CUE_TYPES, WRITABLE_CUE_TYPES, Cue, Track

CUE_SELECT = "SELECT id, track_id, type, position, length, hotcue, label, color FROM cues"
SLOT_REQUIRED = frozenset({"hotcue"})
SLOT_OPTIONAL = frozenset({"loop"})

# Mixxx writes -1 as the "not set" sentinel for a cue position (same convention, same constant
# value, as doctor.py's UNSET_POSITION — duplicated here rather than imported to avoid a
# db -> doctor layering dependency). The raw value is still reported verbatim as
# `position_samples` (like `slot: -1` already is), but a *derived* value must not state a
# falsehood: `-1 / rate` is not a real position in seconds, so `position_seconds` is None instead.
# No comparable sentinel was found for `length`: the fixture has zero negative `length` values
# (checked: `SELECT count(*) FROM cues WHERE length < 0` returns 0), and neither doctor.py nor the
# spec documents one, so `length_seconds` is left as a plain `length / rate` division.
UNSET_POSITION = -1


def row_to_cue(row: sqlite3.Row, samples_per_second: float | None) -> Cue:
    position, length = float(row["position"]), float(row["length"])
    rate = samples_per_second or None
    position_seconds = None if position == UNSET_POSITION else (position / rate if rate else None)
    return Cue(id=row["id"], track_id=row["track_id"], type_id=row["type"], position_samples=position,
               length_samples=length, slot=row["hotcue"], label=row["label"] or "", color=row["color"],
               position_seconds=position_seconds, length_seconds=length / rate if rate else None)


def list_cues(conn: sqlite3.Connection, track: Track, *, type_id: int | None = None) -> list[Cue]:
    sql, params = CUE_SELECT + " WHERE track_id = ?", [track.id]
    if type_id is not None:
        sql, params = sql + " AND type = ?", params + [type_id]
    rows = conn.execute(sql + " ORDER BY type, hotcue, position, id", params).fetchall()
    return [row_to_cue(row, track.samples_per_second) for row in rows]


def get_cue_row(conn: sqlite3.Connection, cue_id: int) -> sqlite3.Row:
    row = conn.execute(CUE_SELECT + " WHERE id = ?", (cue_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"cue {cue_id} does not exist", cue_id=cue_id)
    return row


def parse_cue_type(value: str | int) -> tuple[str, int]:
    text = str(value).strip().lower()
    if text.isdigit() and int(text) in CUE_TYPES:
        return CUE_TYPES[int(text)], int(text)
    if text in CUE_TYPE_IDS:
        return text, CUE_TYPE_IDS[text]
    raise ValidationError(f"unknown cue type {value!r}", type=value, types=sorted(CUE_TYPE_IDS))


def require_writable(type_name: str) -> None:
    if type_name not in WRITABLE_CUE_TYPES:
        raise ValidationError(f"cue type {type_name!r} is read-only in v1; writable types are {sorted(WRITABLE_CUE_TYPES)}",
                              type=type_name, writable=sorted(WRITABLE_CUE_TYPES))


def store_number(value: float) -> int | float:
    """Store whole numbers as INTEGER (as Mixxx mostly does) and fractions as REAL."""
    return int(value) if float(value).is_integer() else float(value)


def validate_cue(conn: sqlite3.Connection, track: Track, *, type_name: str, position: float, length: float,
                 slot: int | None, color: int, exclude_id: int | None = None) -> int:
    require_writable(type_name)
    if color <= 0 or color > 0xFFFFFF:
        raise ValidationError("cue colour must be a non-zero 24-bit RGB value", color=color)
    if type_name in SLOT_REQUIRED and slot is None:
        raise ValidationError(f"--slot is required for {type_name} cues", type=type_name)
    if type_name not in SLOT_REQUIRED | SLOT_OPTIONAL and slot is not None:
        raise ValidationError(f"--slot is not allowed for {type_name} cues", type=type_name, slot=slot)
    if slot is not None and slot < 0:
        raise ValidationError("slot must be >= 0 (0-based; the UI shows it 1-based)", slot=slot)
    if position < 0:
        raise ValidationError("position must be >= 0", position_samples=position)
    if length < 0:
        raise ValidationError("length must be >= 0", length_samples=length)
    limit = (track.audio.duration or 0) * (track.samples_per_second or 0)
    if limit and position + length > limit:
        raise ValidationError(f"position + length ({position + length:g} samples) exceeds the track ({limit:g} samples)",
                              position_samples=position, length_samples=length, track_samples=limit)
    type_id = CUE_TYPE_IDS[type_name]
    if slot is not None:
        sql, params = "SELECT id FROM cues WHERE track_id = ? AND type = ? AND hotcue = ?", [track.id, type_id, slot]
        if exclude_id is not None:
            sql, params = sql + " AND id != ?", params + [exclude_id]
        existing = conn.execute(sql + " ORDER BY id LIMIT 1", params).fetchone()
        if existing is not None:
            raise ValidationError(f"{type_name} slot {slot} is already occupied on track {track.id} by cue {existing[0]}",
                                  track_id=track.id, type=type_name, slot=slot, existing_cue_id=existing[0])
    return -1 if slot is None else slot


def _fetch(conn: sqlite3.Connection, cue_id: int, track: Track) -> Cue:
    return row_to_cue(get_cue_row(conn, cue_id), track.samples_per_second)


def plan_add(conn: sqlite3.Connection, track: Track, *, type_name: str, position: float, length: float,
             slot: int | None, color: int, label: str) -> MutationPlan:
    stored_slot = validate_cue(conn, track, type_name=type_name, position=position, length=length, slot=slot, color=color)
    type_id = CUE_TYPE_IDS[type_name]
    statement = Statement("INSERT INTO cues (track_id, type, position, length, hotcue, label, color) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (track.id, type_id, store_number(position), store_number(length), stored_slot, label, color))
    preview = Cue(id=-1, track_id=track.id, type_id=type_id, position_samples=float(position), length_samples=float(length),
                  slot=stored_slot, label=label, color=color,
                  position_seconds=position / track.samples_per_second if track.samples_per_second else None,
                  length_seconds=length / track.samples_per_second if track.samples_per_second else None)

    def after_commit(conn: sqlite3.Connection) -> dict[str, Any]:
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"cue": _fetch(conn, new_id, track)}

    return MutationPlan(statements=[statement], data={"cue": preview}, after_commit=after_commit)


def plan_set(conn: sqlite3.Connection, cue: Cue, track: Track, *, position: float | None, length: float | None,
             slot: int | None, label: str | None, color: int | None) -> MutationPlan:
    require_writable(cue.type)
    changes: dict[str, Any] = {}
    if position is not None and position != cue.position_samples:
        changes["position_samples"] = {"old": cue.position_samples, "new": float(position)}
    if length is not None and length != cue.length_samples:
        changes["length_samples"] = {"old": cue.length_samples, "new": float(length)}
    if slot is not None and slot != cue.slot:
        changes["slot"] = {"old": cue.slot, "new": slot}
    if label is not None and label != cue.label:
        changes["label"] = {"old": cue.label, "new": label}
    if color is not None and color != cue.color:
        changes["color"] = {"old": cue.color, "new": color}
    if position is None and length is None and slot is None and label is None and color is None:
        raise ValidationError("nothing to set: pass at least one of --position/--length/--slot/--label/--color", cue_id=cue.id)
    new_position = cue.position_samples if position is None else position
    new_length = cue.length_samples if length is None else length
    new_slot = (cue.slot if cue.slot >= 0 else None) if slot is None else slot
    new_color = cue.color if color is None else color
    stored_slot = validate_cue(conn, track, type_name=cue.type, position=new_position, length=new_length, slot=new_slot,
                               color=new_color, exclude_id=cue.id)
    statement = Statement("UPDATE cues SET position = ?, length = ?, hotcue = ?, label = ?, color = ? WHERE id = ?",
                          (store_number(new_position), store_number(new_length), stored_slot,
                           cue.label if label is None else label, new_color, cue.id))
    return MutationPlan(statements=[statement], data={"cue": cue, "changed": changes},
                        after_commit=lambda c: {"cue": _fetch(c, cue.id, track), "changed": changes})


def plan_delete(conn: sqlite3.Connection, cue: Cue) -> MutationPlan:
    require_writable(cue.type)
    return MutationPlan(statements=[Statement("DELETE FROM cues WHERE id = ?", (cue.id,))], data={"deleted": cue})


def plan_clear(conn: sqlite3.Connection, track: Track, *, type_name: str | None) -> MutationPlan:
    if type_name is not None:
        require_writable(type_name)
        type_ids = [CUE_TYPE_IDS[type_name]]
    else:
        type_ids = sorted(CUE_TYPE_IDS[name] for name in WRITABLE_CUE_TYPES)
    marks = ",".join("?" * len(type_ids))
    rows = conn.execute(CUE_SELECT + f" WHERE track_id = ? AND type IN ({marks}) ORDER BY type, hotcue, position, id",
                        [track.id, *type_ids]).fetchall()
    victims = [row_to_cue(r, track.samples_per_second) for r in rows]
    statements = [Statement("DELETE FROM cues WHERE id = ?", (c.id,)) for c in victims]
    return MutationPlan(statements=statements, data={"deleted": victims})
