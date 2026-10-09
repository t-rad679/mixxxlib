#!/usr/bin/env python3
"""Trim a real Mixxx database into the committed test fixture (spec §12.2).

    .venv/bin/python scripts/make-fixture.py trim ~/.mixxx/mixxxdb.sqlite tests/fixtures [--tracks 20]
    .venv/bin/python scripts/make-fixture.py nested tests/fixtures/mixxxdb.sqlite tests/fixtures/mixxxdb-42.sqlite

`trim` copies the source read-only, keeps ~N tracks chosen by rule (not by id), rewrites their
locations to one-second silent audio files generated with ffmpeg and tagged from the database row,
and injects the anomalies doctor must find: hotcues with colour 0 and a duplicate slot, a key/key_id
mismatch, unanalyzed tracks, plus three synthetic crates. `nested` applies PR #16871's revisions 41 and
42 and builds a three-level crate tree with two same-named roots. Both write a manifest JSON.
Refuses to run while Mixxx is running. Requires ffmpeg on PATH and mutagen (dev venv).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from pathlib import Path

try:
    from mutagen import id3
    from mutagen.aiff import AIFF
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
except ImportError:  # pragma: no cover
    sys.exit("mutagen missing: run with .venv/bin/python")

AUDIO_REL = "tests/fixtures/audio"
QUOTAS = {"aiff": 8, "mp3": 6, "flac": 4}
ID3_FRAMES = {"artist": "TPE1", "title": "TIT2", "album": "TALB", "album_artist": "TPE2", "year": "TDRC",
              "genre": "TCON", "composer": "TCOM", "grouping": "TIT1"}
VORBIS_TAGS = {"artist": "ARTIST", "title": "TITLE", "album": "ALBUM", "album_artist": "ALBUMARTIST",
               "year": "DATE", "genre": "GENRE", "composer": "COMPOSER", "grouping": "GROUPING",
               "tracknumber": "TRACKNUMBER", "tracktotal": "TRACKTOTAL", "comment": "COMMENT",
               "bpm": "BPM", "key": "INITIALKEY"}


def refuse_if_mixxx_running() -> None:
    if shutil.which("pgrep") and subprocess.run(["pgrep", "-x", "mixxx"], capture_output=True).returncode == 0:
        sys.exit("Mixxx is running; close it first")


def slug(text: str | None) -> str:
    ascii_text = unicodedata.normalize("NFKD", text or "track").encode("ascii", "ignore").decode()
    return (re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").lower()[:24]) or "track"


def pick_tracks(conn: sqlite3.Connection, total: int) -> list[int]:
    keep: list[int] = []
    filetype = dict(conn.execute("SELECT id, filetype FROM library"))

    def add(sql: str, *params) -> None:
        for (tid,) in conn.execute(sql, params):
            if tid not in keep and len(keep) < total:
                keep.append(tid)

    add("SELECT DISTINCT track_id FROM cues WHERE typeof(position) = 'real' ORDER BY track_id LIMIT 1")
    add("SELECT DISTINCT track_id FROM cues WHERE type = 4 AND hotcue >= 0 ORDER BY track_id LIMIT 1")
    add("SELECT id FROM library WHERE beats IS NULL OR bpm = 0 ORDER BY id LIMIT 2")
    for ext, quota in QUOTAS.items():
        have = sum(1 for t in keep if filetype.get(t) == ext)
        add("SELECT l.id FROM library l WHERE l.filetype = ? AND l.beats IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM cues c WHERE c.track_id = l.id) ORDER BY l.id LIMIT ?", ext, max(0, quota - have))
    add("SELECT id FROM library ORDER BY id")
    return sorted(keep)


def trim(conn: sqlite3.Connection, keep: list[int]) -> None:
    ids = ",".join(map(str, keep))
    conn.executescript(f"""
        DELETE FROM library WHERE id NOT IN ({ids});
        DELETE FROM track_locations WHERE id NOT IN (SELECT location FROM library);
        DELETE FROM cues WHERE track_id NOT IN ({ids});
        DELETE FROM crate_tracks WHERE track_id NOT IN ({ids});
        DELETE FROM PlaylistTracks WHERE track_id NOT IN ({ids});
        DELETE FROM track_analysis WHERE track_id NOT IN ({ids});
        DELETE FROM LibraryHashes;
        DELETE FROM directories;
        INSERT INTO directories (directory) VALUES ('{AUDIO_REL}');
        INSERT INTO LibraryHashes (directory_path, hash, directory_deleted, needs_verification) VALUES ('{AUDIO_REL}', 0, 0, 0);
        UPDATE library SET wavesummaryhex = NULL, coverart_digest = NULL, coverart_location = '', coverart_hash = 0;
        DELETE FROM settings WHERE name LIKE 'mixxx.db.model.%';
    """)


def make_audio(ext: str, out: Path) -> None:
    codec = {"aiff": ["-c:a", "pcm_s16be"], "mp3": ["-c:a", "libmp3lame", "-b:a", "128k"], "flac": ["-c:a", "flac"]}[ext]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", "1", *codec, str(out)], check=True)


def write_tags(path: Path, ext: str, row: sqlite3.Row) -> None:
    def present(value) -> bool:
        return value not in (None, "", "//", 0, 0.0)

    if ext == "flac":
        flac = FLAC(path)
        flac.delete()
        for field_name, tag in VORBIS_TAGS.items():
            value = row[field_name]
            if present(value):
                flac[tag] = [format(value, "g") if isinstance(value, float) else str(value)]
        flac.save()
        return
    audio = AIFF(path) if ext == "aiff" else MP3(path)
    if audio.tags is None:
        audio.add_tags()
    for key in list(audio.tags.keys()):
        del audio.tags[key]
    for field_name, frame in ID3_FRAMES.items():
        if present(row[field_name]):
            audio.tags.add(getattr(id3, frame)(encoding=3, text=[str(row[field_name])]))
    if present(row["tracknumber"]):
        text = f"{row['tracknumber']}/{row['tracktotal']}" if present(row["tracktotal"]) else str(row["tracknumber"])
        audio.tags.add(id3.TRCK(encoding=3, text=[text]))
    if present(row["comment"]):
        audio.tags.add(id3.COMM(encoding=3, lang="eng", desc="", text=[row["comment"]]))
    if present(row["bpm"]):
        audio.tags.add(id3.TBPM(encoding=3, text=[format(row["bpm"], "g")]))
    if present(row["key"]):
        audio.tags.add(id3.TKEY(encoding=3, text=[row["key"]]))
    audio.save()


def relocate(conn: sqlite3.Connection, keep: list[int], audio_dir: Path) -> dict[str, str]:
    audio_dir.mkdir(parents=True, exist_ok=True)
    for old in audio_dir.iterdir():
        old.unlink()
    names: dict[str, str] = {}
    for n, tid in enumerate(keep, 1):
        row = conn.execute("SELECT l.*, tl.id AS tl_id FROM library l JOIN track_locations tl ON tl.id = l.location "
                           "WHERE l.id = ?", (tid,)).fetchone()
        ext = row["filetype"]
        name = f"{n:02d}-{slug(row['title'])}.{ext}"
        path = audio_dir / name
        make_audio(ext, path)
        write_tags(path, ext, row)
        conn.execute("UPDATE track_locations SET location = ?, filename = ?, directory = ?, filesize = ?, "
                     "fs_deleted = 0, needs_verification = 0 WHERE id = ?",
                     (f"{AUDIO_REL}/{name}", name, AUDIO_REL, path.stat().st_size, row["tl_id"]))
        names[str(tid)] = name
    return names


def inject(conn: sqlite3.Connection, keep: list[int]) -> dict:
    analysed = [r[0] for r in conn.execute("SELECT id FROM library WHERE beats IS NOT NULL ORDER BY id")]
    hot, mismatch = analysed[0], analysed[1]
    rate = conn.execute("SELECT samplerate * channels FROM library WHERE id = ?", (hot,)).fetchone()[0]

    def cue(slot: int, seconds: float, label: str, colour: int) -> int:
        cur = conn.execute("INSERT INTO cues (track_id, type, position, length, hotcue, label, color) "
                           "VALUES (?, 1, ?, 0, ?, ?, ?)", (hot, seconds * rate, slot, label, colour))
        return cur.lastrowid

    hotcue_ids = {"slot0": cue(0, 1.0, "drop", 0xC50A08), "zero_colour": cue(1, 2.5, "zero colour", 0),
                  "dup_a": cue(2, 3.0, "dup a", 0xFF8000), "dup_b": cue(2, 4.0, "dup b", 0x00FF00)}
    conn.execute("UPDATE library SET key = 'Am', key_id = 17 WHERE id = ?", (mismatch,))  # Am is 22
    conn.execute("UPDATE library SET color = ? WHERE id = ?", (0xFF8000, hot))

    def crate(name: str, **flags) -> int:
        cols = ["name", *flags]
        cur = conn.execute(f"INSERT INTO crates ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           (name, *flags.values()))
        return cur.lastrowid

    original = [r[0] for r in conn.execute("SELECT id FROM crates ORDER BY id")]
    loops, locked, empty = crate("Fixture Loops"), crate("Locked Crate", locked=1), crate("Empty Crate", show=0)
    members = {"loops": keep[:5], "locked": keep[5:7]}
    conn.executemany("INSERT INTO crate_tracks (crate_id, track_id) VALUES (?, ?)",
                     [(loops, t) for t in members["loops"]] + [(locked, t) for t in members["locked"]])
    return {
        "hotcue_track": hot, "hotcue_rate": rate, "hotcue_ids": hotcue_ids, "key_mismatch_track": mismatch,
        "coloured_track": hot,
        "unanalyzed_tracks": [r[0] for r in conn.execute("SELECT id FROM library WHERE beats IS NULL OR bpm = 0 ORDER BY id")],
        "real_position_cue_track": (conn.execute("SELECT track_id FROM cues WHERE typeof(position) = 'real' "
                                                 "ORDER BY track_id LIMIT 1").fetchone() or [None])[0],
        "crates": {"loops": loops, "locked": locked, "empty": empty, "original": original},
        "crate_members": members,
    }


def cmd_trim(args: argparse.Namespace) -> None:
    refuse_if_mixxx_running()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "mixxxdb.sqlite"
    if target.exists():
        target.unlink()
    source = sqlite3.connect(f"{Path(args.source).resolve().as_uri()}?mode=ro", uri=True)
    dest = sqlite3.connect(target)
    source.backup(dest)
    source.close()
    dest.row_factory = sqlite3.Row
    keep = pick_tracks(dest, args.tracks)
    trim(dest, keep)
    audio = relocate(dest, keep, out_dir / "audio")
    manifest = inject(dest, keep)
    by_ext: dict[str, list[int]] = {}
    for tid, ext in dest.execute("SELECT id, filetype FROM library ORDER BY id"):
        by_ext.setdefault(ext, []).append(tid)
    manifest.update({"tracks": keep, "tracks_by_ext": by_ext, "audio": audio})
    dest.commit()
    dest.execute("VACUUM")
    dest.close()
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {target} with {len(keep)} tracks; manifest at {out_dir / 'manifest.json'}")


REV_41_42 = """
ALTER TABLE crates ADD COLUMN parent_id INTEGER REFERENCES crates(id) DEFAULT NULL;
CREATE TABLE crates_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name varchar(48) NOT NULL,
    count INTEGER DEFAULT 0,
    show INTEGER DEFAULT 1, locked INTEGER DEFAULT 0, autodj_source INTEGER DEFAULT 0,
    parent_id INTEGER REFERENCES crates(id) DEFAULT NULL,
    UNIQUE (parent_id, name));
INSERT INTO crates_new (id, name, count, show, locked, autodj_source, parent_id)
    SELECT id, name, count, show, locked, autodj_source, parent_id FROM crates;
DROP TABLE crates;
ALTER TABLE crates_new RENAME TO crates;
UPDATE settings SET value = '42' WHERE name IN ('mixxx.schema.version', 'mixxx.schema.last_used_version');
"""


def cmd_nested(args: argparse.Namespace) -> None:
    src, dst = Path(args.source), Path(args.dest)
    shutil.copy(src, dst)
    conn = sqlite3.connect(dst)
    conn.executescript(REV_41_42)
    keep = [r[0] for r in conn.execute("SELECT id FROM library ORDER BY id")]

    def crate(name: str, parent: int | None) -> int:
        return conn.execute("INSERT INTO crates (name, parent_id) VALUES (?, ?)", (name, parent)).lastrowid

    genres = crate("Genres", None)
    bass = crate("Bass", genres)
    dubstep = crate("Dubstep", bass)
    dupe_a, dupe_b = crate("Dupe", None), crate("Dupe", None)  # NULL parent_id defeats UNIQUE (parent_id, name)
    members = {"bass": keep[:3], "dubstep": keep[3:5]}
    conn.executemany("INSERT INTO crate_tracks (crate_id, track_id) VALUES (?, ?)",
                     [(bass, t) for t in members["bass"]] + [(dubstep, t) for t in members["dubstep"]])
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    manifest = {"crates": {"genres": genres, "bass": bass, "dubstep": dubstep, "dupe_a": dupe_a, "dupe_b": dupe_b},
                "crate_members": members}
    (dst.parent / "manifest-42.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {dst} at schema 42")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    trim_p = sub.add_parser("trim")
    trim_p.add_argument("source")
    trim_p.add_argument("out_dir")
    trim_p.add_argument("--tracks", type=int, default=20)
    trim_p.set_defaults(func=cmd_trim)
    nested_p = sub.add_parser("nested")
    nested_p.add_argument("source")
    nested_p.add_argument("dest")
    nested_p.set_defaults(func=cmd_nested)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
