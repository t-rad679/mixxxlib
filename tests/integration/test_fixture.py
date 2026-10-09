import sqlite3
from contextlib import closing

from mutagen import File as MutagenFile

from mixxxlib.db.schema import TABLE_COLUMNS


def test_fixture_is_schema_39_with_the_documented_anomalies(sandbox, manifest):
    assert sandbox.scalar("SELECT value FROM settings WHERE name = 'mixxx.schema.version'") == "39"
    assert sandbox.scalar("SELECT count(*) FROM library") == len(manifest["tracks"]) >= 18
    assert sandbox.scalar("SELECT count(*) FROM cues WHERE type = 1 AND color = 0") == 1
    assert sandbox.scalar("SELECT count(*) FROM cues WHERE track_id = ? AND type = 1 AND hotcue = 2", manifest["hotcue_track"]) == 2
    key, key_id = sandbox.query("SELECT key, key_id FROM library WHERE id = ?", manifest["key_mismatch_track"])[0]
    assert (key, key_id) == ("Am", 17)
    assert sandbox.scalar("SELECT count(*) FROM library WHERE beats IS NULL OR bpm = 0") == len(manifest["unanalyzed_tracks"]) >= 1
    assert sandbox.scalar("SELECT count(*) FROM cues WHERE typeof(position) = 'real'") >= 1
    assert {ext for ext in manifest["tracks_by_ext"]} == {"aiff", "mp3", "flac"}
    assert sandbox.scalar("SELECT locked FROM crates WHERE id = ?", manifest["crates"]["locked"]) == 1


def test_fixture_locations_point_at_tagged_sandbox_audio(sandbox, manifest):
    for track_id, name in manifest["audio"].items():
        (path,) = sandbox.query("SELECT tl.location FROM library l JOIN track_locations tl ON tl.id = l.location WHERE l.id = ?", int(track_id))[0]
        assert path == str(sandbox.audio / name)
        audio = MutagenFile(path)
        assert audio is not None and audio.info.length > 0.5


def test_fixture_tables_match_the_pinned_column_sets(sandbox):
    with closing(sandbox.connect()) as conn:
        for table, columns in TABLE_COLUMNS.items():
            actual = tuple(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))
            assert actual == columns, table


def test_fixture_keeps_the_broken_upstream_foreign_keys(sandbox):
    ddl = sandbox.scalar("SELECT sql FROM sqlite_master WHERE name = 'cues'")
    assert 'REFERENCES "library_old"(id)' in ddl


def test_schema_42_fixture(sandbox_42, manifest_42):
    assert sandbox_42.scalar("SELECT value FROM settings WHERE name = 'mixxx.schema.version'") == "42"
    rows = sandbox_42.query("SELECT name, parent_id FROM crates WHERE name = 'Dupe'")
    assert rows == [("Dupe", None), ("Dupe", None)]
    c = manifest_42["crates"]
    assert sandbox_42.scalar("SELECT parent_id FROM crates WHERE id = ?", c["dubstep"]) == c["bass"]
    assert sandbox_42.scalar("SELECT parent_id FROM crates WHERE id = ?", c["bass"]) == c["genres"]
    ddl = sandbox_42.scalar("SELECT sql FROM sqlite_master WHERE name = 'crates'")
    assert "UNIQUE (parent_id, name)" in ddl
