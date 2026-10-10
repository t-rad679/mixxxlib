"""Audio-file tags via mutagen (spec §6.2, §7.4).

MP3 and AIFF carry ID3v2; FLAC (and other Vorbis-comment formats mutagen opens) carry Vorbis comments.
Anything else is refused with TagError.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mutagen
from mutagen import id3
from mutagen._vorbis import VCommentDict

from mixxxlib.errors import TagError
from mixxxlib.safety.backup import TAG_PREFIX, utc_stamp

ID3_SIMPLE = {"artist": "TPE1", "title": "TIT2", "album": "TALB", "album_artist": "TPE2", "year": "TDRC",
              "genre": "TCON", "composer": "TCOM", "bpm": "TBPM", "key": "TKEY"}
VORBIS_TAGS = {"artist": "ARTIST", "title": "TITLE", "album": "ALBUM", "album_artist": "ALBUMARTIST", "year": "DATE",
               "genre": "GENRE", "tracknumber": "TRACKNUMBER", "tracktotal": "TRACKTOTAL", "composer": "COMPOSER",
               "grouping": "GROUPING", "comment": "COMMENT", "bpm": "BPM", "key": "INITIALKEY"}
TAG_FIELDS: frozenset[str] = frozenset(VORBIS_TAGS)


def _text(frames: list) -> str | None:
    if not frames or not getattr(frames[0], "text", None):
        return None
    return str(frames[0].text[0])


class TagFile:
    def __init__(self, path: os.PathLike[str] | str) -> None:
        try:
            self.path = Path(path)
        except TagError:
            raise
        except Exception as exc:  # e.g. TypeError from Path() on a non-path-like value (None, int, ...)
            raise TagError(f"not a valid path: {path!r}: {exc}", path=repr(path)) from exc
        if not self.path.is_file():
            raise TagError(f"audio file not found: {self.path}", path=str(self.path))
        try:
            audio = mutagen.File(self.path)
        except Exception as exc:  # mutagen raises many types
            raise TagError(f"mutagen could not read {self.path}: {exc}", path=str(self.path)) from exc
        if audio is None:
            raise TagError(f"unsupported audio format: {self.path}", path=str(self.path))
        try:
            if isinstance(audio.tags, VCommentDict) or type(audio).__name__ in ("FLAC", "OggVorbis", "OggOpus", "OggFLAC"):
                self.kind = "vorbis"
                if audio.tags is None:
                    audio.add_tags()
            elif isinstance(audio.tags, id3.ID3) or type(audio).__name__ in ("MP3", "AIFF", "WAVE", "DSF"):
                self.kind = "id3"
                if audio.tags is None:
                    audio.add_tags()
            else:
                raise TagError(f"{self.path} has a tag family mixxxlib does not handle ({type(audio).__name__})", path=str(self.path))
        except TagError:
            raise
        except Exception as exc:  # e.g. add_tags() failing on a corrupt container
            raise TagError(f"mutagen could not initialise tags for {self.path}: {exc}", path=str(self.path)) from exc
        self._audio = audio

    # -- read -----------------------------------------------------------------

    def read(self) -> dict[str, str | None]:
        try:
            if self.kind == "vorbis":
                tags = self._audio.tags
                return {field: (tags[tag][0] if tag in tags and tags[tag] else None) for field, tag in VORBIS_TAGS.items()}
            tags = self._audio.tags
            values: dict[str, str | None] = {field: _text(tags.getall(frame)) for field, frame in ID3_SIMPLE.items()}
            trck = _text(tags.getall("TRCK"))
            number, _, total = (trck or "").partition("/")
            values["tracknumber"] = number or None
            values["tracktotal"] = total or None
            values["grouping"] = _text(tags.getall("TIT1")) or _text(tags.getall("GRP1"))
            comments = tags.getall("COMM")
            preferred = [c for c in comments if c.desc == ""] or comments
            values["comment"] = _text(preferred)
            return values
        except TagError:
            raise
        except Exception as exc:  # e.g. a mutagen frame-decoding quirk, or the file changing underneath us
            raise TagError(f"mutagen could not read tags from {self.path}: {exc}", path=str(self.path)) from exc

    # -- write ----------------------------------------------------------------

    def write(self, values: dict[str, str | None]) -> None:
        unknown = set(values) - TAG_FIELDS
        if unknown:
            raise TagError(f"not file-backed fields: {sorted(unknown)}", fields=sorted(unknown))
        try:
            if self.kind == "vorbis":
                self._write_vorbis(values)
            else:
                self._write_id3(values)
            self._audio.save()
        except TagError:
            raise
        except Exception as exc:
            raise TagError(f"mutagen could not write {self.path}: {exc}", path=str(self.path)) from exc

    def _write_vorbis(self, values: dict[str, str | None]) -> None:
        tags = self._audio.tags
        for field, value in values.items():
            tag = VORBIS_TAGS[field]
            if value is None:
                if tag in tags:
                    del tags[tag]
            else:
                tags[tag] = [value]

    def _write_id3(self, values: dict[str, str | None]) -> None:
        tags = self._audio.tags
        current = self.read()
        for field, value in values.items():
            if field in ID3_SIMPLE:
                frame = ID3_SIMPLE[field]
                tags.delall(frame)
                if value is not None:
                    tags.add(getattr(id3, frame)(encoding=3, text=[value]))
            elif field == "grouping":
                # GRP1 is only ever a read fallback for a legacy file with no TIT1 (spec §6.2); a
                # write must clear it too, or a stale GRP1 would shadow a None-removal or keep
                # read() falling back to it instead of the freshly written TIT1.
                tags.delall("TIT1")
                tags.delall("GRP1")
                if value is not None:
                    tags.add(id3.TIT1(encoding=3, text=[value]))
            elif field == "comment":
                for comm in tags.getall("COMM"):
                    if comm.desc == "":
                        tags.delall(comm.HashKey)
                if value is not None:
                    tags.add(id3.COMM(encoding=3, lang="eng", desc="", text=[value]))
        if "tracknumber" in values or "tracktotal" in values:
            number = values.get("tracknumber", current["tracknumber"])
            total = values.get("tracktotal", current["tracktotal"])
            tags.delall("TRCK")
            if total:
                tags.add(id3.TRCK(encoding=3, text=[f"{number or ''}/{total}"]))
            elif number:
                tags.add(id3.TRCK(encoding=3, text=[number]))

    def mtime_ms(self) -> int:
        try:
            return int(self.path.stat().st_mtime * 1000)
        except TagError:
            raise
        except Exception as exc:  # e.g. the file vanishing between write and this stat
            raise TagError(f"could not stat {self.path}: {exc}", path=str(self.path)) from exc


def read_tags(path: os.PathLike[str] | str) -> dict[str, str | None]:
    return TagFile(path).read()


def write_tags(path: os.PathLike[str] | str, values: dict[str, str | None]) -> None:
    TagFile(path).write(values)


def snapshot_tags(path: os.PathLike[str] | str, backup_dir: Path, track_id: int, *, now: datetime | None = None) -> Path:
    """Write a JSON snapshot of `path`'s current tags under `backup_dir`. Always raises TagError."""
    try:
        stamp = utc_stamp(now or datetime.now(timezone.utc))
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"{TAG_PREFIX}{stamp}-{track_id}.json"
        n = 1
        while target.exists():
            n += 1
            target = backup_dir / f"{TAG_PREFIX}{stamp}-{track_id}-{n}.json"
        payload = {"path": str(Path(path)), "track_id": track_id, "taken": stamp, "tags": TagFile(path).read()}
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except TagError:
        raise
    except Exception as exc:
        raise TagError(f"could not snapshot tags for {path}: {exc}", path=str(path), backup_dir=str(backup_dir)) from exc
    return target


def restore_tags(snapshot_path: os.PathLike[str] | str) -> None:
    """Write back the tags recorded in a snapshot produced by `snapshot_tags`. Always raises TagError."""
    try:
        raw = Path(snapshot_path).read_text(encoding="utf-8")
    except Exception as exc:
        raise TagError(f"could not read tag snapshot {snapshot_path}: {exc}", snapshot=str(snapshot_path)) from exc
    try:
        payload = json.loads(raw)
        path = payload["path"]
        tags = payload["tags"]
        values = {field: tags.get(field) for field in TAG_FIELDS}
    except TagError:
        raise
    except Exception as exc:
        raise TagError(f"tag snapshot {snapshot_path} is malformed: {exc}", snapshot=str(snapshot_path)) from exc
    write_tags(path, values)


class TagWriteAction:
    """FileAction for connection.run_mutation: snapshot -> write -> re-read to confirm -> (rollback on demand)."""

    def __init__(self, path: os.PathLike[str] | str, track_id: int, values: dict[str, str | None], backup_dir: Path) -> None:
        self.path = Path(path)
        self.track_id = track_id
        self.values = dict(values)
        self.backup_dir = backup_dir
        self.snapshot: Path | None = None

    def describe(self) -> str:
        return f"write tags {sorted(self.values)} to {self.path}"

    def apply(self) -> dict[str, Any]:
        self.snapshot = snapshot_tags(self.path, self.backup_dir, self.track_id)
        try:
            write_tags(self.path, self.values)
            back = read_tags(self.path)
            mismatches = {field: {"expected": value, "read_back": back.get(field)}
                          for field, value in self.values.items() if back.get(field) != value}
            if mismatches:
                raise TagError(f"tags did not read back as written in {self.path}", path=str(self.path), mismatches=mismatches)
            return {"mtime_ms": TagFile(self.path).mtime_ms(), "snapshot": str(self.snapshot)}
        except TagError as exc:
            # Anything from here on (a failed write, a failed verification read, a mismatch, or a
            # failed mtime_ms stat) means the file may already have been rewritten on disk with
            # self.values, while the database side of this mutation never ran. run_mutation's
            # `applied` bookkeeping only tracks actions whose apply() returned successfully, so it
            # cannot roll this one back itself -- apply() must self-heal before propagating, or the
            # file and database are left silently desynced (the Critical finding this guards).
            try:
                restore_tags(self.snapshot)
                exc.details["file_rollback"] = [self.describe()]
            except TagError as restore_exc:
                exc.details["file_rollback"] = []
                exc.details["file_rollback_failed"] = [self.describe()]
                exc.details["file_rollback_error"] = str(restore_exc)
            raise

    def rollback(self) -> None:
        if self.snapshot is not None:
            restore_tags(self.snapshot)
