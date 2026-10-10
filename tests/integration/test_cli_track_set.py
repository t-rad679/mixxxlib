import os
import sqlite3

import pytest

from mixxxlib.tags.files import read_tags


def path_of(sandbox, manifest, track_id):
    return sandbox.audio / manifest["audio"][str(track_id)]


def test_default_write_updates_file_then_database(sandbox, manifest):
    track_id = manifest["tracks_by_ext"]["aiff"][0]
    path = path_of(sandbox, manifest, track_id)
    r = sandbox.run("track", "set", str(track_id), "artist=New Artist", "title=New Title", "--json")
    assert r.code == 8 and r.error["error"]["code"] == "confirmation_required"
    assert read_tags(path)["artist"] != "New Artist"
    r = sandbox.run("track", "set", str(track_id), "artist=New Artist", "title=New Title", "--yes", "--json")
    assert r.code == 0, r.stderr
    data = r.json["data"]
    assert data["file_written"] is True
    assert data["changed"]["artist"]["new"] == "New Artist" and data["changed"]["title"]["new"] == "New Title"
    assert data["track"]["metadata"]["artist"] == "New Artist"
    assert read_tags(path)["artist"] == "New Artist" and read_tags(path)["title"] == "New Title"
    assert sandbox.scalar("SELECT artist FROM library WHERE id = ?", track_id) == "New Artist"
    assert sandbox.scalar("SELECT source_synchronized_ms FROM library WHERE id = ?", track_id) == int(path.stat().st_mtime * 1000)
    assert r.json["warnings"] == []
    assert r.json["mutation"]["statements"] == 2  # one UPDATE + one file write
    assert any(p.name.startswith("tags-") for p in sandbox.backup_dir.iterdir())
    assert any(p.name.startswith("mixxxdb-") and p.name.endswith("-track-set.sqlite") for p in sandbox.backup_dir.iterdir())


@pytest.mark.parametrize("ext", ["mp3", "flac"])
def test_other_formats_round_trip(sandbox, manifest, ext):
    track_id = manifest["tracks_by_ext"][ext][0]
    r = sandbox.run("track", "set", str(track_id), "genre=Bass", "tracknumber=7", "--yes", "--json")
    assert r.code == 0, r.stderr
    tags = read_tags(path_of(sandbox, manifest, track_id))
    assert tags["genre"] == "Bass" and tags["tracknumber"] == "7"


def test_mixxx_only_leaves_the_file_alone_and_warns(sandbox, manifest):
    track_id = manifest["tracks_by_ext"]["mp3"][1]
    path = path_of(sandbox, manifest, track_id)
    before = read_tags(path)
    mtime = path.stat().st_mtime
    r = sandbox.run("track", "set", str(track_id), "album=DB Only", "--mixxx-only", "--json")
    assert r.code == 0 and r.json["data"]["file_written"] is False
    assert [w["code"] for w in r.json["warnings"]] == ["file_backed"]
    assert read_tags(path) == before and path.stat().st_mtime == mtime
    assert sandbox.scalar("SELECT album FROM library WHERE id = ?", track_id) == "DB Only"


def test_file_only_skips_database_and_backup(sandbox, manifest):
    track_id = manifest["tracks_by_ext"]["flac"][1]
    r = sandbox.run("track", "set", str(track_id), "comment=file side", "--file-only", "--yes", "--json")
    assert r.code == 0 and r.json["mutation"]["backup"] is None and r.json["mutation"]["integrity"] is None
    assert read_tags(path_of(sandbox, manifest, track_id))["comment"] == "file side"
    assert sandbox.scalar("SELECT comment FROM library WHERE id = ?", track_id) != "file side"
    assert not any(p.name.startswith("mixxxdb-") for p in sandbox.backup_dir.iterdir())


def test_caveated_fields_and_key_pairing(sandbox, manifest):
    track_id = manifest["tracks_by_ext"]["aiff"][1]
    r = sandbox.run("track", "set", str(track_id), "bpm=128.5", "key=Em", "--yes", "--json")
    assert r.code == 0
    assert [w["code"] for w in r.json["warnings"]] == ["blob_authoritative"]
    assert sandbox.query("SELECT bpm, key, key_id FROM library WHERE id = ?", track_id) == [(128.5, "Em", 17)]
    assert r.json["data"]["changed"]["key"]["new"] == {"id": 17, "text": "Em"}
    tags = read_tags(path_of(sandbox, manifest, track_id))
    assert tags["bpm"] == "128.5" and tags["key"] == "Em"
    r = sandbox.run("track", "set", str(track_id), "key=17", "--mixxx-only")
    assert r.code == 0 and "blob_authoritative" in r.stderr


def test_database_only_fields(sandbox, manifest):
    track_id = manifest["tracks"][6]
    r = sandbox.run("track", "set", str(track_id), "rating=4", "color=#FF8000", "bpm_lock=true", "url=https://x", "--json")
    assert r.code == 0 and r.json["data"]["file_written"] is False
    assert [w["code"] for w in r.json["warnings"]] == ["no_file_tag"]
    assert sandbox.query("SELECT rating, color, bpm_lock, url FROM library WHERE id = ?", track_id) == [(4, 0xFF8000, 1, "https://x")]
    for bad in ("rating=7", "color=blue", "bpm=-1", "bpm_lock=maybe", "nope=1", "noequals", "key=H"):
        r = sandbox.run("track", "set", str(track_id), bad, "--json")
        assert r.code == 4, bad


def test_clear_sets_null_and_removes_the_tag(sandbox, manifest):
    track_id = manifest["tracks_by_ext"]["aiff"][2]
    path = path_of(sandbox, manifest, track_id)
    sandbox.run("track", "set", str(track_id), "comment=temp", "--yes")
    r = sandbox.run("track", "set", str(track_id), "--clear", "comment", "--clear", "key", "--yes", "--json")
    assert r.code == 0
    assert sandbox.query("SELECT comment, key, key_id FROM library WHERE id = ?", track_id) == [(None, "", 0)]
    tags = read_tags(path)
    assert tags["comment"] is None and tags["key"] is None
    r = sandbox.run("track", "set", str(track_id), "comment=x", "--clear", "comment", "--json")
    assert r.code == 4


def test_flags_and_dry_run(sandbox, manifest):
    track_id = manifest["tracks"][0]
    r = sandbox.run("track", "set", str(track_id), "artist=x", "--mixxx-only", "--file-only", "--json")
    assert r.code == 4
    r = sandbox.run("track", "set", str(track_id), "artist=Dry", "--dry-run", "--json")
    assert r.code == 0 and r.json["mutation"]["dry_run"] is True
    ops = r.json["mutation"]["statements"]
    assert any("<mtime_ms>" in op for op in ops) and any(op.startswith("write tags ['artist']") for op in ops)
    assert sandbox.scalar("SELECT artist FROM library WHERE id = ?", track_id) != "Dry"
    assert not sandbox.backup_dir.exists()


def test_database_failure_after_file_write_restores_the_file(sandbox, manifest, monkeypatch):
    from mixxxlib.db import tracks as tracks_sql

    track_id = manifest["tracks_by_ext"]["mp3"][2]
    path = path_of(sandbox, manifest, track_id)
    before = read_tags(path)
    monkeypatch.setattr(tracks_sql, "update_sql", lambda columns: "UPDATE no_such_table SET x = ? WHERE id = ?")
    r = sandbox.run("track", "set", str(track_id), "artist=Doomed", "--yes", "--json")
    assert r.code == 7
    err = r.error["error"]
    assert err["details"]["backup"].endswith(".sqlite") and err["details"]["file_rollback"]
    assert read_tags(path) == before
    assert sandbox.scalar("SELECT artist FROM library WHERE id = ?", track_id) == before["artist"]


def test_track_set_by_path(sandbox, manifest):
    track_id = manifest["tracks"][1]
    path = path_of(sandbox, manifest, track_id)
    r = sandbox.run("track", "set", str(path), "--paths", "rating=5", "--json")
    assert r.code == 0 and r.json["data"]["track"]["id"] == track_id


def test_unwritable_file_fails_as_tag_error_and_leaves_the_database_unchanged(sandbox, manifest):
    """The two-store failure mode the architecture exists for: the file write (step 8) happens
    before the one database transaction (step 9), so a step-8 failure must never leave a changed row."""
    track_id = manifest["tracks_by_ext"]["aiff"][3]
    path = path_of(sandbox, manifest, track_id)
    before_artist = sandbox.scalar("SELECT artist FROM library WHERE id = ?", track_id)
    mode = path.stat().st_mode
    os.chmod(path, 0o444)
    try:
        r = sandbox.run("track", "set", str(track_id), "artist=Unwritable", "--yes", "--json")
    finally:
        os.chmod(path, mode)
    assert r.code == 9, r.stderr
    assert r.error["error"]["code"] == "tag_error"
    assert sandbox.scalar("SELECT artist FROM library WHERE id = ?", track_id) == before_artist
