CUE_KEYS = ["id", "track_id", "type", "type_id", "slot", "position_samples", "position_seconds", "length_samples",
            "length_seconds", "label", "color", "color_hex"]


def test_cue_list_shape_and_order(sandbox, manifest):
    track_id = manifest["hotcue_track"]
    r = sandbox.run("cue", "list", str(track_id), "--json")
    assert r.code == 0
    cues = r.json["data"]
    assert list(cues[0]) == CUE_KEYS
    keys = [(c["type_id"], c["slot"], c["position_samples"]) for c in cues]
    assert keys == sorted(keys)
    hot = [c for c in cues if c["type"] == "hotcue"]
    assert len(hot) == 4 and hot[0]["slot"] == 0 and hot[0]["position_seconds"] == 1.0
    assert hot[0]["color_hex"] == "#C50A08" and hot[0]["position_samples"] == manifest["hotcue_rate"] * 1.0
    r = sandbox.run("cue", "list", str(track_id), "--type", "hotcue", "--json")
    assert len(r.json["data"]) == 4
    r = sandbox.run("cue", "list", str(track_id))
    # Not a literal "id  type" prefix: this fixture's cue ids are 4 digits wide, so the shared
    # table() column-width logic (also exercised by crate/track list, which do use 2-char ids and
    # so do match that literal prefix) pads the "id" column wider than its 2-char header, pushing
    # "type" further right. Checking the header's column order (not its exact spacing) is what
    # this assertion is actually after.
    assert r.code == 0 and r.stdout.splitlines()[0].split()[:2] == ["id", "type"]


def test_track_show_includes_cues(sandbox, manifest):
    r = sandbox.run("track", "show", str(manifest["hotcue_track"]), "--json")
    assert len(r.json["data"]["cues"]) == sandbox.scalar("SELECT count(*) FROM cues WHERE track_id = ?", manifest["hotcue_track"])


def test_cue_add_hotcue_and_validation(sandbox, manifest):
    track_id = manifest["tracks_by_ext"]["mp3"][0]
    rate = sandbox.scalar("SELECT samplerate * channels FROM library WHERE id = ?", track_id)
    r = sandbox.run("cue", "add", str(track_id), "--type", "hotcue", "--position", "2.5", "--slot", "0", "--color", "#C50A08", "--label", "drop", "--json")
    assert r.code == 0, r.stderr
    cue = r.json["data"]["cue"]
    assert cue["type"] == "hotcue" and cue["slot"] == 0 and cue["position_samples"] == 2.5 * rate and cue["position_seconds"] == 2.5
    assert sandbox.query("SELECT type, position, length, hotcue, label, color FROM cues WHERE id = ?", cue["id"]) == [(1, 2.5 * rate, 0, 0, "drop", 0xC50A08)]
    r = sandbox.run("cue", "add", str(track_id), "--type", "hotcue", "--position", "3", "--slot", "0", "--color", "#00FF00", "--json")
    assert r.code == 4 and r.error["error"]["details"]["existing_cue_id"] == cue["id"]
    cases = [
        (["--type", "hotcue", "--position", "3", "--slot", "1", "--color", "0"], "zero colour"),
        (["--type", "hotcue", "--position", "3", "--color", "#00FF00"], "slot required"),
        (["--type", "intro", "--position", "3", "--slot", "1", "--color", "#00FF00"], "slot forbidden"),
        (["--type", "main", "--position", "3", "--color", "#00FF00"], "read-only type"),
        (["--type", "beat", "--position", "3", "--color", "#00FF00"], "never written"),
        (["--type", "intro", "--position", "99999", "--color", "#00FF00"], "beyond duration"),
        (["--type", "intro", "--position", "-1", "--color", "#00FF00"], "negative"),
        (["--type", "loop", "--position", "1", "--length", "-2", "--color", "#00FF00"], "negative length"),
        (["--type", "intro", "--position", "1", "--position-samples", "5", "--color", "#00FF00"], "both position forms"),
        (["--type", "intro", "--color", "#00FF00"], "no position"),
    ]
    for args, why in cases:
        r = sandbox.run("cue", "add", str(track_id), *args, "--json")
        assert r.code == 4, why
    r = sandbox.run("cue", "add", str(track_id), "--type", "loop", "--position-samples", str(rate), "--length-samples", str(rate * 4), "--slot", "3", "--color", "0xFF8000", "--json")
    assert r.code == 0 and r.json["data"]["cue"]["length_seconds"] == 4.0
    assert sandbox.query("SELECT typeof(position) FROM cues WHERE id = ?", r.json["data"]["cue"]["id"]) == [("integer",)]


def test_cue_set_delete_clear(sandbox, manifest):
    track_id = manifest["hotcue_track"]
    cue_id = manifest["hotcue_ids"]["slot0"]
    r = sandbox.run("cue", "set", str(cue_id), "--position", "5", "--label", "moved", "--json")
    assert r.code == 0 and r.json["data"]["cue"]["position_seconds"] == 5.0
    assert set(r.json["data"]["changed"]) == {"position_samples", "label"}
    r = sandbox.run("cue", "set", str(cue_id), "--slot", "2", "--json")
    assert r.code == 4  # slot 2 is occupied (twice) in the fixture
    r = sandbox.run("cue", "set", str(cue_id), "--json")
    assert r.code == 4
    r = sandbox.run("cue", "delete", str(cue_id), "--json")
    assert r.code == 8
    r = sandbox.run("cue", "delete", str(cue_id), "--yes", "--json")
    assert r.code == 0 and r.json["data"]["deleted"]["id"] == cue_id
    assert sandbox.scalar("SELECT count(*) FROM cues WHERE id = ?", cue_id) == 0
    r = sandbox.run("cue", "delete", "999999", "--yes", "--json")
    assert r.code == 3
    read_only_before = sandbox.scalar("SELECT count(*) FROM cues WHERE track_id = ? AND type IN (2, 8)", track_id)
    r = sandbox.run("cue", "clear", str(track_id), "--type", "hotcue", "--yes", "--json")
    assert r.code == 0 and len(r.json["data"]["deleted"]) == 3
    r = sandbox.run("cue", "clear", str(track_id), "--yes", "--json")
    assert r.code == 0
    assert sandbox.scalar("SELECT count(*) FROM cues WHERE track_id = ? AND type IN (2, 8)", track_id) == read_only_before
    assert sandbox.scalar("SELECT count(*) FROM cues WHERE track_id = ? AND type IN (1, 4, 5, 6, 7)", track_id) == 0
    r = sandbox.run("cue", "clear", str(track_id), "--type", "main", "--yes", "--json")
    assert r.code == 4


def test_cue_dry_run(sandbox, manifest):
    track_id = manifest["tracks"][0]
    before = sandbox.scalar("SELECT count(*) FROM cues WHERE track_id = ?", track_id)
    r = sandbox.run("cue", "add", str(track_id), "--type", "outro", "--position", "1", "--color", "#111111", "--dry-run", "--json")
    assert r.code == 0 and r.json["mutation"]["statements"][0].startswith("INSERT INTO cues")
    assert sandbox.scalar("SELECT count(*) FROM cues WHERE track_id = ?", track_id) == before
