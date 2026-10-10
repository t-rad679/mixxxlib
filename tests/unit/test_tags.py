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


def test_read_wraps_a_non_tag_error_as_tag_error(tmp_path, monkeypatch):
    # Regression for the final-review Critical: TagFile.read() had no exception wrapping at all
    # (unlike __init__/write()), so any non-TagError raised while decoding frames escaped
    # run_mutation's `except TagError` and bypassed rollback entirely.
    path = copy(tmp_path, "mp3")

    def boom(frames):
        raise ValueError("simulated mutagen decoding quirk")

    monkeypatch.setattr(files, "_text", boom)
    with pytest.raises(TagError):
        files.TagFile(path).read()


def test_mtime_ms_wraps_a_non_tag_error_as_tag_error(tmp_path):
    # Same bug class as above: mtime_ms() called self.path.stat() unwrapped. A file removed in
    # the narrow window between the verification read succeeding and this stat (e.g. by something
    # else touching the file) must come back as a TagError, not a bare FileNotFoundError.
    path = copy(tmp_path, "mp3")
    tf = files.TagFile(path)
    path.unlink()
    with pytest.raises(TagError):
        tf.mtime_ms()


def test_unsupported_and_missing_files_are_tag_errors(tmp_path):
    text = tmp_path / "notes.txt"
    text.write_text("hi")
    with pytest.raises(TagError):
        files.read_tags(text)
    with pytest.raises(TagError) as e:
        files.read_tags(tmp_path / "missing.mp3")
    assert e.value.details["path"].endswith("missing.mp3")


# -- Finding 2: grouping=None (and overwrite) must clear a legacy GRP1 fallback too --------------

def _grp1_only(tmp_path):
    path = copy(tmp_path, "aiff")
    audio = AIFF(path)
    audio.tags.delall("TIT1")
    audio.tags.add(id3.GRP1(encoding=3, text=["legacy"]))
    audio.save()
    return path


def test_grouping_clear_removes_legacy_grp1_fallback(tmp_path):
    path = _grp1_only(tmp_path)
    assert files.read_tags(path)["grouping"] == "legacy"
    files.write_tags(path, {"grouping": None})
    assert files.read_tags(path)["grouping"] is None
    audio = AIFF(path)
    assert "GRP1" not in audio.tags and "TIT1" not in audio.tags


def test_grouping_overwrite_on_grp1_only_file_leaves_no_stale_fallback(tmp_path):
    path = _grp1_only(tmp_path)
    files.write_tags(path, {"grouping": "fresh"})
    assert files.read_tags(path)["grouping"] == "fresh"
    audio = AIFF(path)
    assert "GRP1" not in audio.tags
    assert str(audio.tags["TIT1"].text[0]) == "fresh"


# -- Finding 3: snapshot/restore of a field that was ABSENT beforehand, per dialect --------------

def test_snapshot_restore_of_previously_absent_field_id3(tmp_path):
    path = copy(tmp_path, "mp3")
    before = files.read_tags(path)
    assert before["composer"] is None  # genuinely absent on this fixture
    snap = files.snapshot_tags(path, tmp_path / "backups", 1)
    files.write_tags(path, {"composer": "New Composer"})
    assert files.read_tags(path)["composer"] == "New Composer"
    files.restore_tags(snap)
    after = files.read_tags(path)
    assert after["composer"] is None
    assert after == before


def test_snapshot_restore_of_previously_absent_field_vorbis(tmp_path):
    path = copy(tmp_path, "flac")
    before = files.read_tags(path)
    assert before["composer"] is None  # genuinely absent on this fixture
    snap = files.snapshot_tags(path, tmp_path / "backups", 1)
    files.write_tags(path, {"composer": "New Composer"})
    assert files.read_tags(path)["composer"] == "New Composer"
    files.restore_tags(snap)
    after = files.read_tags(path)
    assert after["composer"] is None
    assert after == before


# -- Finding 1: every exception out of this module, including OS/parse failures, is TagError -----

def test_snapshot_tags_wraps_os_errors_as_tag_error(tmp_path):
    path = copy(tmp_path, "mp3")
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")  # backup_dir's parent is a file, so mkdir() must fail
    backup_dir = blocker / "backups"
    with pytest.raises(TagError) as e:
        files.snapshot_tags(path, backup_dir, 1)
    assert e.value.exit_code == 9


def test_restore_tags_missing_snapshot_is_tag_error(tmp_path):
    with pytest.raises(TagError) as e:
        files.restore_tags(tmp_path / "missing-snapshot.json")
    assert e.value.exit_code == 9


def test_restore_tags_malformed_json_is_tag_error(tmp_path):
    snap = tmp_path / "bad.json"
    snap.write_text("{not valid json")
    with pytest.raises(TagError) as e:
        files.restore_tags(snap)
    assert e.value.exit_code == 9


def test_restore_tags_snapshot_missing_required_keys_is_tag_error(tmp_path):
    snap = tmp_path / "incomplete.json"
    snap.write_text(json.dumps({"path": "whatever"}))  # no "tags" key
    with pytest.raises(TagError) as e:
        files.restore_tags(snap)
    assert e.value.exit_code == 9


def test_constructor_wraps_add_tags_failure_as_tag_error(tmp_path, monkeypatch):
    path = copy(tmp_path, "mp3")

    class FakeMP3:
        tags = None

        def add_tags(self):
            raise RuntimeError("boom")

    FakeMP3.__name__ = "MP3"
    monkeypatch.setattr(files.mutagen, "File", lambda p: FakeMP3())
    with pytest.raises(TagError) as e:
        files.TagFile(path)
    assert e.value.exit_code == 9


@pytest.mark.parametrize("bad_path", [None, 123])
def test_restore_tags_snapshot_with_non_path_like_path_is_tag_error(tmp_path, bad_path):
    snap = tmp_path / "bad-path.json"
    snap.write_text(json.dumps({"path": bad_path, "track_id": 1, "tags": {f: None for f in files.TAG_FIELDS}}))
    with pytest.raises(TagError) as e:
        files.restore_tags(snap)
    assert e.value.exit_code == 9
