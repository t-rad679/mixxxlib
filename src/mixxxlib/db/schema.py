"""Schema revisions, capabilities and the column sets mixxxlib relies on (spec §3, §7.3).

Supporting a new revision means adding a row to REVISION_CAPABILITIES and, after verifying
against a real database, raising SCHEMA_CEILING and adding the revision to VERIFIED_REVISIONS.
"""

from __future__ import annotations

import sqlite3

from mixxxlib.errors import SchemaUnsupportedError
from mixxxlib.model import SchemaInfo, WarningMessage

SCHEMA_CEILING = 40
SCHEMA_FLOOR = 39
VERIFIED_REVISIONS: frozenset[int] = frozenset({39})
# res/schema.xml at the Mixxx 2.5.6 release commit, which carries revision 39.
SCHEMA_SOURCE = "https://github.com/mixxxdj/mixxx/blob/94ccc34f/res/schema.xml"

# What each known revision adds. Capabilities are cumulative.
REVISION_CAPABILITIES: dict[int, frozenset[str]] = {
    39: frozenset(),
    40: frozenset({"tuning_frequency"}),        # library.tuning_frequency_hz, nullable
    41: frozenset({"nested_crates"}),           # PR #16871: crates.parent_id
    42: frozenset({"sibling_unique_rebuild"}),  # PR #16871: UNIQUE (parent_id, name) replaces UNIQUE (name)
}

# The tables and columns mixxxlib reads or writes, as found at revision 39.
TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "library": (
        "id", "artist", "title", "album", "year", "genre", "tracknumber", "location", "comment", "url",
        "duration", "bitrate", "samplerate", "cuepoint", "bpm", "wavesummaryhex", "channels", "datetime_added",
        "mixxx_deleted", "played", "header_parsed", "filetype", "replaygain", "timesplayed", "rating", "key",
        "beats", "beats_version", "composer", "bpm_lock", "beats_sub_version", "keys", "keys_version",
        "keys_sub_version", "key_id", "grouping", "album_artist", "coverart_source", "coverart_type",
        "coverart_location", "coverart_hash", "replaygain_peak", "tracktotal", "color", "coverart_color",
        "coverart_digest", "last_played_at", "source_synchronized_ms",
    ),
    "track_locations": ("id", "location", "filename", "directory", "filesize", "fs_deleted", "needs_verification"),
    "crates": ("id", "name", "count", "show", "locked", "autodj_source"),
    "crate_tracks": ("crate_id", "track_id"),
    "cues": ("id", "track_id", "type", "position", "length", "hotcue", "label", "color"),
    "settings": ("name", "value", "locked", "hidden"),
    "Playlists": ("id", "name", "position", "hidden", "date_created", "date_modified", "locked"),
    "PlaylistTracks": ("id", "playlist_id", "track_id", "position", "pl_datetime_added"),
    "track_analysis": ("id", "track_id", "type", "description", "version", "created", "data_checksum"),
}


def capabilities_for(version: int) -> frozenset[str]:
    caps: set[str] = set()
    for revision, added in REVISION_CAPABILITIES.items():
        if revision <= version:
            caps |= added
    return frozenset(caps)


def first_revision_with(capability: str) -> int:
    for revision in sorted(REVISION_CAPABILITIES):
        if capability in REVISION_CAPABILITIES[revision]:
            return revision
    raise KeyError(capability)


def read_schema_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE name = 'mixxx.schema.version'").fetchone()
    return None if row is None else row[0]


def schema_info(conn: sqlite3.Connection, *, ceiling: int = SCHEMA_CEILING) -> SchemaInfo:
    try:
        raw = read_schema_version(conn)
    except sqlite3.Error as exc:
        raw = None
        unreadable = str(exc)
    else:
        unreadable = ""
    try:
        version: int | None = int(raw) if raw is not None else None
    except ValueError:
        version = None
    if version is None:
        message = unreadable or f"schema version row is missing or unreadable (found {raw!r})"
        return SchemaInfo(None, raw, ceiling, frozenset(), "missing", message)
    caps = capabilities_for(version)
    if version > ceiling:
        verdict, message = "unsupported", f"schema revision {version} is above the supported ceiling {ceiling}"
    elif version < SCHEMA_FLOOR:
        verdict, message = "unsupported", f"schema revision {version} is below the lowest known revision {SCHEMA_FLOOR}"
    elif version in VERIFIED_REVISIONS:
        verdict, message = "supported", f"schema revision {version} is supported"
    else:
        verdict, message = "unverified", f"schema revision {version} is allowed but has never been exercised against a real database"
    return SchemaInfo(version, raw, ceiling, caps, verdict, message)


def require_schema(info: SchemaInfo, *, capability: str | None = None) -> list[WarningMessage]:
    """Raise SchemaUnsupportedError when the schema cannot be mutated; return warnings otherwise."""
    if info.verdict in ("missing", "unsupported"):
        raise SchemaUnsupportedError(info.message, version=info.version, ceiling=info.ceiling)
    if capability is not None and capability not in info.capabilities:
        needs = first_revision_with(capability)
        raise SchemaUnsupportedError(
            f"{capability} needs schema revision {needs}; this database is revision {info.version}",
            version=info.version, ceiling=info.ceiling, capability=capability, needs=needs,
        )
    if info.verdict == "unverified":
        return [WarningMessage("schema_unverified", info.message, {"version": info.version})]
    return []


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))
