"""All SQL for library + track_locations (spec §6.2, §8.3). Always join; never assume id == location."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from mixxxlib.blobs import beats
from mixxxlib.db.connection import Deferred, MutationPlan, Statement
from mixxxlib.errors import MixxxlibError, NotFoundError, ValidationError
from mixxxlib.model import (
    Analysis, Audio, Bookkeeping, CoverArt, Location, Metadata, MusicalKey, Track, WarningMessage, parse_color,
)
from mixxxlib.tags.files import TagWriteAction

# field name → library column. `key` also writes key_id (never independently).
WRITABLE_FIELDS: dict[str, str] = {
    "artist": "artist", "title": "title", "album": "album", "album_artist": "album_artist", "year": "year",
    "genre": "genre", "tracknumber": "tracknumber", "tracktotal": "tracktotal", "composer": "composer",
    "grouping": "grouping", "comment": "comment", "rating": "rating", "color": "color", "url": "url",
    "bpm_lock": "bpm_lock", "bpm": "bpm", "key": "key",
}
FILE_BACKED_FIELDS: frozenset[str] = frozenset({
    "artist", "title", "album", "album_artist", "year", "genre", "tracknumber", "tracktotal", "composer",
    "grouping", "comment", "bpm", "key",
})
BLOB_AUTHORITATIVE_FIELDS: frozenset[str] = frozenset({"bpm", "key"})

# Bounded sample size for aggregated warnings (matches doctor.py's SAMPLE convention).
SAMPLE = 10


def _select(has_tuning: bool) -> str:
    tuning = "l.tuning_frequency_hz" if has_tuning else "NULL"
    return (
        f"SELECT l.*, {tuning} AS tuning_frequency_hz, tl.id AS tl_id, tl.location AS tl_location, "
        "tl.filename AS tl_filename, tl.directory AS tl_directory, tl.filesize AS tl_filesize, "
        "tl.fs_deleted AS tl_fs_deleted FROM library l JOIN track_locations tl ON tl.id = l.location"
    )


def _float(value) -> float | None:
    return None if value is None else float(value)


def _int(value) -> int | None:
    return None if value is None else int(value)


def _bool(value) -> bool | None:
    return None if value is None else bool(value)


def row_to_track(row: sqlite3.Row) -> tuple[Track, list[WarningMessage]]:
    grid, warnings = beats.decode(row["beats"], row["beats_version"], row["samplerate"])
    track = Track(
        id=row["id"],
        location=Location(id=row["tl_id"], path=row["tl_location"], filename=row["tl_filename"],
                          directory=row["tl_directory"], filesize=_int(row["tl_filesize"]), fs_deleted=_bool(row["tl_fs_deleted"])),
        metadata=Metadata(artist=row["artist"], title=row["title"], album=row["album"], album_artist=row["album_artist"],
                          year=row["year"], genre=row["genre"], tracknumber=row["tracknumber"], tracktotal=row["tracktotal"],
                          composer=row["composer"], grouping=row["grouping"], comment=row["comment"],
                          rating=_int(row["rating"]), color=_int(row["color"]), url=row["url"], bpm_lock=_bool(row["bpm_lock"]),
                          bpm=_float(row["bpm"]), key=MusicalKey.from_db(row["key"], row["key_id"])),
        audio=Audio(duration=_float(row["duration"]), bitrate=_int(row["bitrate"]), samplerate=_int(row["samplerate"]),
                    channels=_int(row["channels"]), filetype=row["filetype"], replaygain=_float(row["replaygain"]),
                    replaygain_peak=_float(row["replaygain_peak"]), tuning_frequency_hz=_float(row["tuning_frequency_hz"])),
        bookkeeping=Bookkeeping(datetime_added=row["datetime_added"], played=_bool(row["played"]),
                                timesplayed=_int(row["timesplayed"]), last_played_at=row["last_played_at"],
                                mixxx_deleted=_bool(row["mixxx_deleted"]), header_parsed=_bool(row["header_parsed"]),
                                source_synchronized_ms=_int(row["source_synchronized_ms"])),
        analysis=Analysis(beats_version=row["beats_version"], beats_sub_version=row["beats_sub_version"],
                          keys_version=row["keys_version"], keys_sub_version=row["keys_sub_version"], beatgrid=grid),
        coverart=CoverArt(source=_int(row["coverart_source"]), type=_int(row["coverart_type"]),
                          location=row["coverart_location"], hash=_int(row["coverart_hash"]), color=_int(row["coverart_color"])),
    )
    return track, warnings


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _aggregate_warnings(per_track: list[tuple[int, list[WarningMessage]]]) -> list[WarningMessage]:
    """Collapse per-row decode warnings (spec §6.4: beats_undecodable, unknown_blob_version) into
    one WarningMessage per distinct code, naming a bounded sample of affected track ids.

    A library with hundreds of unanalyzed tracks must not emit hundreds of warning lines — the same
    false-positive-noise failure mode a doctor check hit in Task 11. `list_tracks` can return
    arbitrarily many rows, so it aggregates; `get_track`/`get_track_by_path` return at most one
    row's warnings and pass them through unaggregated (this helper, with one row, degenerates to
    exactly that).
    """
    by_code: dict[str, list[int]] = {}
    for track_id, warnings in per_track:
        for warning in warnings:
            by_code.setdefault(warning.code, []).append(track_id)
    return [
        WarningMessage(code, f"{len(ids)} track(s) raised a {code!r} warning while decoding the beatgrid",
                       {"count": len(ids), "track_ids": sorted(ids)[:SAMPLE]})
        for code, ids in by_code.items()
    ]


def list_tracks(conn: sqlite3.Connection, *, has_tuning: bool, crate_ids: list[int] | None = None,
                search: str | None = None) -> tuple[list[Track], list[WarningMessage]]:
    sql, params, where = _select(has_tuning), [], []
    if crate_ids is not None:
        if not crate_ids:
            return [], []
        marks = ",".join("?" * len(crate_ids))
        sql = sql.replace("SELECT l.*", "SELECT DISTINCT l.*", 1)
        sql += " JOIN crate_tracks ct ON ct.track_id = l.id"
        where.append(f"ct.crate_id IN ({marks})")
        params.extend(crate_ids)
    if search:
        pattern = f"%{escape_like(search)}%"
        where.append("(l.artist LIKE ? ESCAPE '\\' OR l.title LIKE ? ESCAPE '\\' OR l.album LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern, pattern])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY l.id"
    pairs = [row_to_track(row) for row in conn.execute(sql, params)]
    tracks = [track for track, _ in pairs]
    warnings = _aggregate_warnings([(track.id, track_warnings) for track, track_warnings in pairs])
    return tracks, warnings


def get_track(conn: sqlite3.Connection, track_id: int, *, has_tuning: bool) -> tuple[Track, list[WarningMessage]]:
    row = conn.execute(_select(has_tuning) + " WHERE l.id = ?", (track_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"track {track_id} does not exist", track_id=track_id)
    return row_to_track(row)


def get_track_by_path(conn: sqlite3.Connection, path: str, *, has_tuning: bool) -> tuple[Track, list[WarningMessage]]:
    absolute = os.path.abspath(path)
    row = conn.execute(_select(has_tuning) + " WHERE tl.location = ?", (absolute,)).fetchone()
    if row is None:
        raise NotFoundError(f"no track at {absolute}", path=absolute)
    return row_to_track(row)


def resolve_track_ids(conn: sqlite3.Connection, selectors: Iterable[str], *, paths: bool,
                      missing_error: type[MixxxlibError] = ValidationError) -> list[int]:
    """Ids as ints; with `paths`, file paths resolved through track_locations.location after abspath."""
    ids: list[int] = []
    unknown: list[str] = []
    for selector in selectors:
        if paths:
            absolute = os.path.abspath(selector)
            row = conn.execute("SELECT l.id FROM library l JOIN track_locations tl ON tl.id = l.location WHERE tl.location = ?",
                               (absolute,)).fetchone()
            if row is None:
                unknown.append(absolute)
            else:
                ids.append(row[0])
        else:
            try:
                ids.append(int(selector))
            except ValueError:
                raise ValidationError(f"track id must be an integer, got {selector!r} (use --paths for file paths)", value=selector) from None
    if unknown:
        raise missing_error(f"{len(unknown)} path(s) are not in the library: {unknown[:5]}", paths=unknown)
    return ids


# ---------------------------------------------------------------- track set (spec §7.4)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def coerce_field(name: str, raw: str) -> Any:
    """Type/range validation only (spec constraint 7). Returns the database value."""
    if name not in WRITABLE_FIELDS:
        raise ValidationError(f"unknown field {name!r}", field=name, fields=sorted(WRITABLE_FIELDS))
    if name == "rating":
        value = _coerce_int(raw, name)
        if not 0 <= value <= 5:
            raise ValidationError(f"rating must be 0..5, got {value}", field=name, value=raw)
        return value
    if name == "color":
        return parse_color(raw, allow_zero=True)
    if name == "bpm":
        try:
            value = float(raw)
        except ValueError:
            raise ValidationError(f"bpm must be a number, got {raw!r}", field=name, value=raw) from None
        if value < 0:
            raise ValidationError(f"bpm must be >= 0, got {value}", field=name, value=raw)
        return value
    if name == "bpm_lock":
        lowered = raw.strip().lower()
        if lowered in _TRUE:
            return 1
        if lowered in _FALSE:
            return 0
        raise ValidationError(f"bpm_lock must be true/false, got {raw!r}", field=name, value=raw)
    if name == "key":
        return MusicalKey.parse(raw)
    if name in FILE_BACKED_FIELDS and raw == "":
        # mutagen drops an empty-text ID3 frame on save() (mp3/aiff) but keeps an empty Vorbis
        # comment (flac) — the same `FIELD=` would silently behave differently by format. Reject
        # it uniformly instead of letting it through: `--clear FIELD` already means "remove this".
        raise ValidationError(f"{name} cannot be set to an empty value; use --clear {name} to remove it instead",
                              field=name)
    return str(raw)


def _coerce_int(raw: str, name: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise ValidationError(f"{name} must be an integer, got {raw!r}", field=name, value=raw) from None


def file_tag_value(name: str, value: Any) -> str | None:
    """The string mutagen should write for a coerced database value; None removes the tag."""
    if value is None:
        return None
    if name == "key":
        return value.text or None
    if name == "bpm":
        return format(value, "g")
    return str(value)


def update_sql(columns: list[str]) -> str:
    assignments = ", ".join(f"{column} = ?" for column in columns)
    return f"UPDATE library SET {assignments} WHERE id = ?"


def validate_set(changed_fields: Iterable[str], clears: Iterable[str], *, write_db: bool) -> None:
    """Pure, track-independent checks on a `track set` request's shape — shared by the CLI (called
    ahead of the --yes gate, with the raw FIELD names) and `plan_set` (called with the coerced
    `changes` dict's keys), so the two surfaces can never diverge on what counts as a valid request:
    unknown or overlapping fields, nothing to set, and fields that would land in no store at all
    under the given --mixxx-only/--file-only scope. Raises ValidationError; never returns a value.
    """
    changed_fields = set(changed_fields)
    clears = set(clears)
    overlap = changed_fields & clears
    if overlap:
        raise ValidationError(f"fields both set and cleared: {sorted(overlap)}", fields=sorted(overlap))
    for name in clears:
        if name not in WRITABLE_FIELDS:
            raise ValidationError(f"unknown field {name!r}", field=name, fields=sorted(WRITABLE_FIELDS))
    if not changed_fields and not clears:
        raise ValidationError("nothing to set: pass FIELD=VALUE or --clear FIELD")
    touched = changed_fields | clears
    db_only = touched - FILE_BACKED_FIELDS
    if not write_db and db_only:
        # These fields have no audio-file tag, so with --file-only (write_db=False) nothing would
        # write them at all — not even partially. Reject the whole request rather than silently
        # drop part of it (and rather than warn-and-continue, which previously claimed `changed`
        # for a field that was never actually written anywhere).
        raise ValidationError(
            f"{sorted(db_only)} have no audio-file tag; --file-only cannot write them to any store",
            fields=sorted(db_only))


def plan_set(conn: sqlite3.Connection, track: Track, changes: dict[str, Any], clears: set[str], *,
             write_file: bool, write_db: bool, backup_dir: Path) -> MutationPlan:
    """Build the §7.4 plan: file action (if any file-backed field is in scope) then one UPDATE."""
    validate_set(changes, clears, write_db=write_db)

    new_values: dict[str, Any] = {**changes, **{name: None for name in clears}}
    if "key" in clears:
        # Decision 3: `--clear key` writes key='' and key_id=0 (Mixxx's own defaults), not NULL —
        # key_id is never set independently of key, and key's own default is ''. Every other
        # `--clear` writes NULL.
        new_values["key"] = MusicalKey(0, "")

    touched = set(new_values)
    file_fields = touched & FILE_BACKED_FIELDS
    db_only_fields = touched - FILE_BACKED_FIELDS

    warnings: list[WarningMessage] = []
    if touched & BLOB_AUTHORITATIVE_FIELDS:
        warnings.append(WarningMessage("blob_authoritative",
                                       "bpm/key were written, but the beats/keys blob is authoritative and Mixxx may discard the change",
                                       {"fields": sorted(touched & BLOB_AUTHORITATIVE_FIELDS)}))
    if not write_file and file_fields:
        warnings.append(WarningMessage("file_backed",
                                       "these fields are re-imported from the audio file; the file's value wins the next time Mixxx re-reads it",
                                       {"fields": sorted(file_fields)}))
    if write_file and db_only_fields:
        # write_file and db_only_fields here implies write_db is True: validate_set already rejects
        # db-only fields outright when write_db is False, so the database is guaranteed to be the
        # one store that did get written for them.
        warnings.append(WarningMessage("no_file_tag", "these fields have no audio-file counterpart, so they were written to the database only",
                                       {"fields": sorted(db_only_fields)}))

    old_values = {name: getattr(track.metadata, name) for name in touched}
    changed = {name: {"old": old_values[name], "new": new_values[name]} for name in sorted(touched)}

    if write_file and file_fields and not track.location.path:
        raise ValidationError("track has no file location to write tags to", track_id=track.id)
    file_written = bool(write_file and file_fields and track.location.path)

    file_actions: list[TagWriteAction] = []
    if file_written:
        tag_values = {name: file_tag_value(name, new_values[name]) for name in sorted(file_fields)}
        file_actions.append(TagWriteAction(track.location.path, track.id, tag_values, backup_dir))

    statements: list[Statement] = []
    if write_db:
        columns: list[str] = []
        params: list[Any] = []
        for name in sorted(touched):
            value = new_values[name]
            if name == "key":
                columns += ["key", "key_id"]
                params += [value.text, value.id]
            else:
                columns.append(WRITABLE_FIELDS[name])
                params.append(value)
        if file_written:
            columns.append("source_synchronized_ms")
            params.append(Deferred("mtime_ms"))
        statements.append(Statement(update_sql(columns), (*params, track.id)))

    def after_commit(c: sqlite3.Connection) -> dict[str, Any]:
        has_tuning = any(row[1] == "tuning_frequency_hz" for row in c.execute("PRAGMA table_info(library)"))
        fresh, _ = get_track(c, track.id, has_tuning=has_tuning)
        return {"track": fresh, "changed": changed, "file_written": file_written}

    return MutationPlan(statements=statements, data={"track": track, "changed": changed, "file_written": file_written},
                        warnings=warnings, file_actions=file_actions, after_commit=after_commit if write_db else None)
