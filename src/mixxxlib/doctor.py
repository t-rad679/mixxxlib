"""Pure-read health checks (spec §8.5). Every check returns findings; nothing is modified."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mixxxlib.blobs.beats import KNOWN_VERSIONS
from mixxxlib.db.connection import integrity_check
from mixxxlib.errors import ValidationError
from mixxxlib.model import CUE_TYPES, Finding, MusicalKey, SchemaInfo
from mixxxlib.safety import backup as backup_mod

KNOWN_KEYS_VERSIONS = frozenset({"KeyMap-1.0"})
SAMPLE = 10

# Mixxx writes -1 as the "not set" sentinel for a cue position (observed on `outro` cues in the
# fixture; the convention is documented as applying to other cue types too, e.g. `intro`). -1 is
# not a real negative position, so it must not trip either half of the outside-track test below.
UNSET_POSITION = -1

# Tolerance (in the same unit as cues.position: interleaved samples, i.e. seconds * samplerate *
# channels) for the "cue ends beyond the track" test. `library.duration` is a float whose rounding
# can put `duration * samplerate * channels` a fraction of a sample below a cue that in fact ends
# exactly at the track's end — observed in the fixture as a ~7.5e-5-sample overshoot (cue 2603).
# One full sample is ~13,000x that observed noise, while a genuinely misplaced cue is expected to be
# off by many samples (a different unit, a dropped multiplication by `channels`, a stale cue carried
# over from a re-encoded file, etc.), so this tolerance absorbs the float noise without being loose
# enough to hide a real one.
OUTSIDE_TRACK_TOLERANCE = 1.0


@dataclass
class DoctorContext:
    conn: sqlite3.Connection
    schema: SchemaInfo
    backup_dir: Path
    process_check: Callable[[], bool | None]
    nested: bool


def _ids(ctx: DoctorContext, sql: str, *params) -> list[int]:
    return [r[0] for r in ctx.conn.execute(sql, params)]


def check_schema(ctx: DoctorContext) -> list[Finding]:
    severity = {"supported": "info", "unverified": "warning"}.get(ctx.schema.verdict, "error")
    return [Finding("schema", severity, ctx.schema.message,
                    {"version": ctx.schema.version, "ceiling": ctx.schema.ceiling,
                     "capabilities": sorted(ctx.schema.capabilities), "verdict": ctx.schema.verdict})]


def check_mixxx_running(ctx: DoctorContext) -> list[Finding]:
    running = ctx.process_check()
    text = {True: "Mixxx is running", False: "Mixxx is not running", None: "cannot tell whether Mixxx is running (no pgrep)"}[running]
    return [Finding("mixxx_running", "info", text, {"process": running})]


def check_integrity(ctx: DoctorContext) -> list[Finding]:
    result = integrity_check(ctx.conn)
    severity = "info" if result == "ok" else "error"
    return [Finding("integrity", severity, f"PRAGMA integrity_check: {result}", {"result": result})]


ORPHAN_QUERIES: list[tuple[str, str]] = [
    ("crate_tracks.crate_id -> crates.id",
     "SELECT ct.track_id FROM crate_tracks ct LEFT JOIN crates c ON c.id = ct.crate_id WHERE c.id IS NULL ORDER BY ct.track_id"),
    ("crate_tracks.track_id -> library.id",
     "SELECT ct.track_id FROM crate_tracks ct LEFT JOIN library l ON l.id = ct.track_id WHERE l.id IS NULL ORDER BY ct.track_id"),
    ("cues.track_id -> library.id",
     "SELECT cu.id FROM cues cu LEFT JOIN library l ON l.id = cu.track_id WHERE l.id IS NULL ORDER BY cu.id"),
    ("PlaylistTracks.playlist_id -> Playlists.id",
     "SELECT pt.id FROM PlaylistTracks pt LEFT JOIN Playlists p ON p.id = pt.playlist_id WHERE p.id IS NULL ORDER BY pt.id"),
    ("PlaylistTracks.track_id -> library.id",
     "SELECT pt.id FROM PlaylistTracks pt LEFT JOIN library l ON l.id = pt.track_id WHERE l.id IS NULL ORDER BY pt.id"),
    ("library.location -> track_locations.id",
     "SELECT l.id FROM library l LEFT JOIN track_locations tl ON tl.id = l.location WHERE tl.id IS NULL ORDER BY l.id"),
    ("track_analysis.track_id -> library.id",
     "SELECT ta.id FROM track_analysis ta LEFT JOIN library l ON l.id = ta.track_id WHERE l.id IS NULL ORDER BY ta.id"),
]


def check_orphans(ctx: DoctorContext) -> list[Finding]:
    findings = []
    queries = list(ORPHAN_QUERIES)
    if ctx.nested:
        queries.append(("crates.parent_id -> crates.id",
                        "SELECT c.id FROM crates c LEFT JOIN crates p ON p.id = c.parent_id WHERE c.parent_id IS NOT NULL AND p.id IS NULL ORDER BY c.id"))
    for relation, sql in queries:
        ids = _ids(ctx, sql)
        if ids:
            findings.append(Finding("orphans", "error", f"{len(ids)} row(s) violate {relation}",
                                    {"relation": relation, "count": len(ids), "sample_ids": ids[:SAMPLE]}))
    if ctx.nested:
        parents = dict(ctx.conn.execute("SELECT id, parent_id FROM crates"))
        cyclic = []
        for start in parents:
            node, steps = parents[start], 0
            while node is not None and steps <= len(parents):
                if node == start:
                    cyclic.append(start)
                    break
                node, steps = parents.get(node), steps + 1
        if cyclic:
            findings.append(Finding("orphans", "error", f"{len(cyclic)} crate(s) sit in a parent_id cycle",
                                    {"relation": "crates.parent_id cycle", "count": len(cyclic), "sample_ids": sorted(cyclic)[:SAMPLE]}))
    return findings


def check_crates(ctx: DoctorContext) -> list[Finding]:
    parent = "parent_id" if ctx.nested else "NULL"
    rows = ctx.conn.execute(
        f"SELECT name, {parent} AS parent_id, count(*) AS n FROM crates GROUP BY name, {parent} "
        f"HAVING n > 1 ORDER BY name, {parent}").fetchall()
    if not rows:
        return []
    duplicates = [{"name": r["name"], "parent_id": r["parent_id"], "count": r["n"]} for r in rows]
    return [Finding("crates", "warning", f"{len(rows)} crate name(s) are duplicated among siblings", {"duplicates": duplicates})]


def check_missing_files(ctx: DoctorContext) -> list[Finding]:
    findings = []
    deleted = _ids(ctx, "SELECT l.id FROM library l JOIN track_locations tl ON tl.id = l.location WHERE tl.fs_deleted = 1 ORDER BY l.id")
    if deleted:
        findings.append(Finding("missing_files", "warning", f"{len(deleted)} track(s) are flagged fs_deleted",
                                {"reason": "fs_deleted", "count": len(deleted), "track_ids": deleted[:SAMPLE]}))
    gone = [tid for tid, loc in ctx.conn.execute(
        "SELECT l.id, tl.location FROM library l JOIN track_locations tl ON tl.id = l.location WHERE tl.fs_deleted = 0 OR tl.fs_deleted IS NULL ORDER BY l.id")
        if not loc or not os.path.exists(loc)]
    if gone:
        findings.append(Finding("missing_files", "warning", f"{len(gone)} track file(s) do not exist on disk",
                                {"reason": "not_on_disk", "count": len(gone), "track_ids": gone[:SAMPLE]}))
    return findings


def check_unanalyzed(ctx: DoctorContext) -> list[Finding]:
    ids = _ids(ctx, "SELECT id FROM library WHERE beats IS NULL OR bpm IS NULL OR bpm = 0 ORDER BY id")
    if not ids:
        return []
    return [Finding("unanalyzed", "info", f"{len(ids)} track(s) have no beatgrid or a zero BPM", {"count": len(ids), "track_ids": ids})]


def check_cues(ctx: DoctorContext) -> list[Finding]:
    findings = []
    zero = _ids(ctx, "SELECT id FROM cues WHERE type = 1 AND color = 0 ORDER BY id")
    if zero:
        findings.append(Finding("cues", "warning", f"{len(zero)} hotcue(s) have colour 0 and will be discarded by Mixxx",
                                {"reason": "zero_colour", "count": len(zero), "cue_ids": zero[:SAMPLE]}))
    dup_rows = ctx.conn.execute("SELECT track_id, type, hotcue, count(*) AS n FROM cues WHERE hotcue >= 0 "
                                "GROUP BY track_id, type, hotcue HAVING n > 1 ORDER BY track_id, type, hotcue").fetchall()
    if dup_rows:
        findings.append(Finding("cues", "warning", f"{len(dup_rows)} (track, type, slot) combination(s) have duplicate cues",
                                {"reason": "duplicate_slot", "count": len(dup_rows),
                                 "slots": [{"track_id": r[0], "type_id": r[1], "slot": r[2], "count": r[3]} for r in dup_rows[:SAMPLE]]}))
    outside = _ids(ctx, "SELECT c.id FROM cues c JOIN library l ON l.id = c.track_id WHERE c.position != ? AND ("
                        "c.position < 0 OR "
                        "(l.duration IS NOT NULL AND l.samplerate IS NOT NULL AND l.channels IS NOT NULL AND "
                        "c.position + c.length > l.duration * l.samplerate * l.channels + ?)) ORDER BY c.id",
                   UNSET_POSITION, OUTSIDE_TRACK_TOLERANCE)
    if outside:
        findings.append(Finding("cues", "warning", f"{len(outside)} cue(s) lie outside their track",
                                {"reason": "outside_track", "count": len(outside), "cue_ids": outside[:SAMPLE]}))
    known = ",".join(str(t) for t in CUE_TYPES)
    unknown = _ids(ctx, f"SELECT id FROM cues WHERE type NOT IN ({known}) ORDER BY id")
    if unknown:
        findings.append(Finding("cues", "warning", f"{len(unknown)} cue(s) have an unknown type",
                                {"reason": "unknown_type", "count": len(unknown), "cue_ids": unknown[:SAMPLE]}))
    return findings


def check_keys(ctx: DoctorContext) -> list[Finding]:
    bad = []
    for track_id, text, key_id in ctx.conn.execute("SELECT id, key, key_id FROM library ORDER BY id"):
        try:
            parsed = MusicalKey.parse(text)
            key_id_int = int(key_id or 0)
        except (ValidationError, ValueError, TypeError):
            # ValidationError from MusicalKey.parse (bad key text); ValueError/TypeError from
            # int() -- SQLite's dynamic typing allows non-numeric text in a nominally-INTEGER
            # key_id column, which must be reported as a `keys` finding, not crash the whole
            # doctor report (final-review item 3).
            bad.append(track_id)
            continue
        if parsed.id != key_id_int:
            bad.append(track_id)
    if not bad:
        return []
    return [Finding("keys", "warning", f"{len(bad)} track(s) have key text and key_id that disagree", {"count": len(bad), "track_ids": bad[:SAMPLE]})]


def check_blob_versions(ctx: DoctorContext) -> list[Finding]:
    findings = []
    beats = [r[0] for r in ctx.conn.execute("SELECT DISTINCT beats_version FROM library WHERE beats_version IS NOT NULL") if r[0] not in KNOWN_VERSIONS]
    if beats:
        findings.append(Finding("blob_versions", "warning", f"unrecognised beats_version(s): {sorted(beats)}", {"column": "beats_version", "versions": sorted(beats)}))
    keys = [r[0] for r in ctx.conn.execute("SELECT DISTINCT keys_version FROM library WHERE keys_version IS NOT NULL") if r[0] not in KNOWN_KEYS_VERSIONS]
    if keys:
        findings.append(Finding("blob_versions", "warning", f"unrecognised keys_version(s): {sorted(keys)}", {"column": "keys_version", "versions": sorted(keys)}))
    return findings


def check_backups(ctx: DoctorContext) -> list[Finding]:
    entries = [e for e in backup_mod.list_backups(ctx.backup_dir) if e.kind == "database"]
    details = {"backup_dir": str(ctx.backup_dir), "count": len(entries),
               "newest": entries[0].name if entries else None, "oldest": entries[-1].name if entries else None}
    return [Finding("backups", "info", f"{len(entries)} database backup(s) in {ctx.backup_dir}", details)]


CHECKS: list[Callable[[DoctorContext], list[Finding]]] = [
    check_schema, check_mixxx_running, check_integrity, check_orphans, check_crates, check_missing_files,
    check_unanalyzed, check_cues, check_keys, check_blob_versions, check_backups,
]


def run_doctor(ctx: DoctorContext) -> list[Finding]:
    """Run every check, isolating failures so one broken check can't discard the rest of the
    report (final-review item 3). `doctor` is the command reached for when something is already
    wrong, and it is pure-read (spec §8.5) -- there is nothing to roll back here, so catching
    broadly and turning a check's own failure into an error-severity Finding is safe and, unlike
    `run_mutation`, costs nothing. A database bad enough to break every check still degrades to a
    report that is all `error` findings rather than no report at all; this also lets `check_schema`
    and `check_integrity`'s own correct diagnoses ("this isn't a Mixxx database") survive even
    when a later check raises on the same broken file."""
    findings: list[Finding] = []
    for check in CHECKS:
        name = check.__name__.removeprefix("check_")
        try:
            findings.extend(check(ctx))
        except Exception as exc:
            findings.append(Finding(name, "error", f"check failed: {type(exc).__name__}: {exc}",
                                    {"exception": type(exc).__name__}))
    return findings


def summarize(findings: list[Finding]) -> dict[str, int]:
    return {"errors": sum(f.severity == "error" for f in findings),
            "warnings": sum(f.severity == "warning" for f in findings),
            "info": sum(f.severity == "info" for f in findings)}
