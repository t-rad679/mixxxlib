import json
import shutil
from pathlib import Path

import pytest
from mutagen import id3
from mutagen.aiff import AIFF

from mixxxlib.errors import TagError
from mixxxlib.tags import files

FIXTURE_AUDIO = Path(__file__).resolve().parents[1] / "fixtures" / "audio"
ALL = {"artist": "A", "title": "T", "album": "L", "album_artist": "AA", "year": "2019", "genre": "G",
       "tracknumber": "3", "tracktotal": "12", "composer": "C", "grouping": "GR", "comment": "hello",
       "bpm": "128", "key": "Em"}


def copy(tmp_path, ext):
    src = next(FIXTURE_AUDIO.glob(f"*.{ext}"))
    dst = tmp_path / src.name
    shutil.copy(src, dst)
    return dst


@pytest.mark.parametrize("ext", ["aiff", "mp3", "flac"])
def test_round_trip_all_fields(tmp_path, ext):
    path = copy(tmp_path, ext)
    assert set(files.read_tags(path)) == files.TAG_FIELDS
    files.write_tags(path, ALL)
    assert files.read_tags(path) == ALL
    files.write_tags(path, {"album": None, "tracktotal": None, "comment": None})
    back = files.read_tags(path)
    assert back["album"] is None and back["tracktotal"] is None and back["comment"] is None
    assert back["tracknumber"] == "3" and back["artist"] == "A"


def test_trck_combines_number_and_total(tmp_path):
    path = copy(tmp_path, "mp3")
    files.write_tags(path, {"tracknumber": "3", "tracktotal": "12"})
    assert str(files.TagFile(path)._audio.tags["TRCK"].text[0]) == "3/12"
    files.write_tags(path, {"tracknumber": None})
    assert str(files.TagFile(path)._audio.tags["TRCK"].text[0]) == "/12"
    files.write_tags(path, {"tracktotal": None})
    assert "TRCK" not in files.TagFile(path)._audio.tags


def test_grouping_reads_grp1_as_fallback_and_writes_tit1(tmp_path):
    path = copy(tmp_path, "aiff")
    audio = AIFF(path)
    audio.tags.delall("TIT1")
    audio.tags.add(id3.GRP1(encoding=3, text=["legacy"]))
    audio.save()
    assert files.read_tags(path)["grouping"] == "legacy"
    files.write_tags(path, {"grouping": "new"})
    audio = AIFF(path)
    assert str(audio.tags["TIT1"].text[0]) == "new"
    assert files.read_tags(path)["grouping"] == "new"


def test_comment_only_touches_the_undescribed_frame(tmp_path):
    path = copy(tmp_path, "mp3")
    audio = files.TagFile(path)._audio
    audio.tags.add(id3.COMM(encoding=3, lang="eng", desc="iTunNORM", text=["keep me"]))
    audio.save()
    files.write_tags(path, {"comment": "mine"})
    comms = files.TagFile(path)._audio.tags.getall("COMM")
    assert {c.desc: c.text[0] for c in comms} == {"iTunNORM": "keep me", "": "mine"}
    assert files.read_tags(path)["comment"] == "mine"


def test_snapshot_and_restore(tmp_path):
    path = copy(tmp_path, "flac")
    before = files.read_tags(path)
    snap = files.snapshot_tags(path, tmp_path / "backups", 42)
    assert snap.name.startswith("tags-") and snap.name.endswith("-42.json")
    payload = json.loads(snap.read_text())
    assert payload["path"] == str(path) and payload["track_id"] == 42 and payload["tags"] == before
    files.write_tags(path, {"artist": "Changed", "title": None})
    files.restore_tags(snap)
    assert files.read_tags(path) == before


def test_tag_write_action_verifies_and_reports_mtime(tmp_path):
    path = copy(tmp_path, "aiff")
    action = files.TagWriteAction(path, 7, {"artist": "Verified"}, tmp_path / "backups")
    assert "artist" in action.describe() and str(path) in action.describe()
    result = action.apply()
    assert result["mtime_ms"] == int(path.stat().st_mtime * 1000)
    assert Path(result["snapshot"]).exists()
    assert files.read_tags(path)["artist"] == "Verified"
    action.rollback()
    assert files.read_tags(path)["artist"] != "Verified"


def test_tag_write_action_restores_on_verification_mismatch(tmp_path, monkeypatch):
    path = copy(tmp_path, "mp3")
    original = files.read_tags(path)
    real_read = files.read_tags
    monkeypatch.setattr(files, "read_tags", lambda p: {**real_read(p), "artist": "WRONG"})
    action = files.TagWriteAction(path, 7, {"artist": "Wanted"}, tmp_path / "backups")
    with pytest.raises(TagError) as e:
        action.apply()
    assert e.value.details["mismatches"] == {"artist": {"expected": "Wanted", "read_back": "WRONG"}}
    monkeypatch.undo()
    assert files.read_tags(path) == original


def test_unsupported_and_missing_files_are_tag_errors(tmp_path):
    text = tmp_path / "notes.txt"
    text.write_text("hi")
    with pytest.raises(TagError):
        files.read_tags(text)
    with pytest.raises(TagError) as e:
        files.read_tags(tmp_path / "missing.mp3")
    assert e.value.details["path"].endswith("missing.mp3")
