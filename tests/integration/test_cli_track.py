import pytest

TRACK_KEYS = ["id", "location", "metadata", "audio", "bookkeeping", "analysis", "coverart"]
METADATA_KEYS = ["artist", "title", "album", "album_artist", "year", "genre", "tracknumber", "tracktotal", "composer",
                 "grouping", "comment", "rating", "color", "url", "bpm_lock", "bpm", "key"]


def test_track_list_json_is_sorted_by_id_with_the_documented_shape(sandbox, manifest):
    r = sandbox.run("track", "list", "--json")
    assert r.code == 0
    tracks = r.json["data"]
    assert [t["id"] for t in tracks] == manifest["tracks"]
    first = tracks[0]
    assert list(first) == TRACK_KEYS
    assert list(first["metadata"]) == METADATA_KEYS
    assert list(first["location"]) == ["id", "path", "filename", "directory", "filesize", "fs_deleted"]
    assert list(first["audio"]) == ["duration", "bitrate", "samplerate", "channels", "filetype", "replaygain", "replaygain_peak", "tuning_frequency_hz"]
    assert list(first["analysis"]) == ["beats_version", "beats_sub_version", "keys_version", "keys_sub_version", "beatgrid"]
    assert set(first["metadata"]["key"]) == {"id", "text"}
    assert first["audio"]["tuning_frequency_hz"] is None
    assert first["location"]["path"].startswith(str(sandbox.audio))


def test_json_is_byte_identical_across_runs(sandbox):
    assert sandbox.run("track", "list", "--json").stdout == sandbox.run("track", "list", "--json").stdout


def test_nulls_stay_null_and_beatgrid_decodes(sandbox, manifest):
    r = sandbox.run("track", "show", str(manifest["unanalyzed_tracks"][0]), "--json")
    data = r.json["data"]
    assert data["track"]["analysis"]["beatgrid"] == {"version": None, "decoded": False, "bpm": None, "first_beat_frame": None, "first_beat_seconds": None}
    r = sandbox.run("track", "show", str(manifest["hotcue_track"]), "--json")
    track = r.json["data"]["track"]
    grid = track["analysis"]["beatgrid"]
    assert grid["decoded"] is True and grid["bpm"] == track["metadata"]["bpm"]
    assert grid["first_beat_seconds"] == pytest.approx(grid["first_beat_frame"] / track["audio"]["samplerate"])
    assert r.json["data"]["beatgrid"] == grid
    assert track["metadata"]["color"] == 0xFF8000
    assert sandbox.scalar("SELECT count(*) FROM library WHERE album IS NULL") == sum(1 for t in sandbox.run("track", "list", "--json").json["data"] if t["metadata"]["album"] is None)


def test_track_list_filters(sandbox, manifest):
    r = sandbox.run("track", "list", "--crate", "Fixture Loops", "--json")
    assert [t["id"] for t in r.json["data"]] == sorted(manifest["crate_members"]["loops"])
    r = sandbox.run("track", "list", "--crate", str(manifest["crates"]["loops"]), "--id", "--json")
    assert len(r.json["data"]) == 5
    title = sandbox.scalar("SELECT title FROM library WHERE id = ?", manifest["tracks"][0])
    r = sandbox.run("track", "list", "--search", title.upper()[:4], "--json")
    assert manifest["tracks"][0] in [t["id"] for t in r.json["data"]]
    r = sandbox.run("track", "list", "--search", "100%_nothing", "--json")
    assert r.code == 0 and r.json["data"] == []


def test_track_show_by_path_and_not_found(sandbox, manifest):
    track_id = manifest["tracks"][2]
    path = sandbox.audio / manifest["audio"][str(track_id)]
    r = sandbox.run("track", "show", str(path), "--paths", "--json")
    assert r.code == 0 and r.json["data"]["track"]["id"] == track_id
    r = sandbox.run("track", "show", "999999", "--json")
    assert r.code == 3 and r.error["error"]["details"] == {"track_id": 999999}
    r = sandbox.run("track", "show", "/nowhere.aiff", "--paths", "--json")
    assert r.code == 3 and r.error["error"]["details"] == {"path": "/nowhere.aiff"}
    r = sandbox.run("track", "show", "abc", "--json")
    assert r.code == 4


def test_track_human_output(sandbox, manifest):
    r = sandbox.run("track", "list")
    assert r.code == 0 and r.stdout.splitlines()[0].startswith("id  ")
    r = sandbox.run("track", "show", str(manifest["tracks"][0]))
    assert r.code == 0 and "metadata:" in r.stdout


def test_recursive_crate_listing_at_42(sandbox_42, manifest_42):
    r = sandbox_42.run("track", "list", "--crate", "Genres", "--recursive", "--json")
    assert [t["id"] for t in r.json["data"]] == sorted(manifest_42["crate_members"]["bass"] + manifest_42["crate_members"]["dubstep"])
    r = sandbox_42.run("track", "list", "--crate", "Dupe", "--json")
    assert r.code == 4


def test_recursive_needs_41(sandbox):
    r = sandbox.run("track", "list", "--crate", "Fixture Loops", "--recursive", "--json")
    assert r.code == 6 and r.error["error"]["details"]["needs"] == 41
