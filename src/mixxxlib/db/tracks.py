"""All SQL for library + track_locations (spec §6.2, §8.3). Always join; never assume id == location."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable

from mixxxlib.blobs import beats
from mixxxlib.errors import MixxxlibError, NotFoundError, ValidationError
from mixxxlib.model import (
    Analysis, Audio, Bookkeeping, CoverArt, Location, Metadata, MusicalKey, Track, WarningMessage,
)

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


def row_to_track(row: sqlite3.Row) -> tuple[Track, list[WarningMessage]]:
    grid, warnings = beats.decode(row["beats"], row["beats_version"], row["samplerate"])
    track = Track(
        id=row["id"],
        location=Location(id=row["tl_id"], path=row["tl_location"], filename=row["tl_filename"],
                          directory=row["tl_directory"], filesize=_int(row["tl_filesize"]), fs_deleted=bool(row["tl_fs_deleted"])),
        metadata=Metadata(artist=row["artist"], title=row["title"], album=row["album"], album_artist=row["album_artist"],
                          year=row["year"], genre=row["genre"], tracknumber=row["tracknumber"], tracktotal=row["tracktotal"],
                          composer=row["composer"], grouping=row["grouping"], comment=row["comment"],
                          rating=_int(row["rating"]), color=_int(row["color"]), url=row["url"], bpm_lock=bool(row["bpm_lock"]),
                          bpm=_float(row["bpm"]), key=MusicalKey.from_db(row["key"], row["key_id"])),
        audio=Audio(duration=_float(row["duration"]), bitrate=_int(row["bitrate"]), samplerate=_int(row["samplerate"]),
                    channels=_int(row["channels"]), filetype=row["filetype"], replaygain=_float(row["replaygain"]),
                    replaygain_peak=_float(row["replaygain_peak"]), tuning_frequency_hz=_float(row["tuning_frequency_hz"])),
        bookkeeping=Bookkeeping(datetime_added=row["datetime_added"], played=bool(row["played"]),
                                timesplayed=_int(row["timesplayed"]), last_played_at=row["last_played_at"],
                                mixxx_deleted=bool(row["mixxx_deleted"]), header_parsed=bool(row["header_parsed"]),
                                source_synchronized_ms=_int(row["source_synchronized_ms"])),
        analysis=Analysis(beats_version=row["beats_version"], beats_sub_version=row["beats_sub_version"],
                          keys_version=row["keys_version"], keys_sub_version=row["keys_sub_version"], beatgrid=grid),
        coverart=CoverArt(source=_int(row["coverart_source"]), type=_int(row["coverart_type"]),
                          location=row["coverart_location"], hash=_int(row["coverart_hash"]), color=_int(row["coverart_color"])),
    )
    return track, warnings


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_tracks(conn: sqlite3.Connection, *, has_tuning: bool, crate_ids: list[int] | None = None,
                search: str | None = None) -> list[Track]:
    sql, params, where = _select(has_tuning), [], []
    if crate_ids is not None:
        if not crate_ids:
            return []
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
    return [row_to_track(row)[0] for row in conn.execute(sql, params)]


def get_track(conn: sqlite3.Connection, track_id: int, *, has_tuning: bool) -> Track:
    row = conn.execute(_select(has_tuning) + " WHERE l.id = ?", (track_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"track {track_id} does not exist", track_id=track_id)
    return row_to_track(row)[0]


def get_track_by_path(conn: sqlite3.Connection, path: str, *, has_tuning: bool) -> Track:
    absolute = os.path.abspath(path)
    row = conn.execute(_select(has_tuning) + " WHERE tl.location = ?", (absolute,)).fetchone()
    if row is None:
        raise NotFoundError(f"no track at {absolute}", path=absolute)
    return row_to_track(row)[0]


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
