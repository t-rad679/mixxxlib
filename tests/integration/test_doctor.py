import sqlite3

from mixxxlib.doctor import summarize


def by_check(findings, name):
    return [f for f in findings if f.check == name]


def test_fixture_doctor_has_no_errors_and_the_expected_warnings(sandbox, manifest):
    findings = sandbox.library().doctor()
    summary = summarize(findings)
    assert summary["errors"] == 0
    assert [f.check for f in findings][:3] == ["schema", "mixxx_running", "integrity"]
    cues = by_check(findings, "cues")
    reasons = {f.details["reason"] for f in cues}
    assert {"zero_colour", "duplicate_slot"} <= reasons
    zero = next(f for f in cues if f.details["reason"] == "zero_colour")
    assert zero.severity == "warning" and manifest["hotcue_ids"]["zero_colour"] in zero.details["cue_ids"]
    keys = by_check(findings, "keys")
    assert len(keys) == 1 and keys[0].details["track_ids"] == [manifest["key_mismatch_track"]]
    unanalyzed = by_check(findings, "unanalyzed")
    assert unanalyzed[0].severity == "info" and unanalyzed[0].details["track_ids"] == manifest["unanalyzed_tracks"]
    assert by_check(findings, "integrity")[0].severity == "info"
    assert by_check(findings, "backups")[0].details["count"] == 0


def test_orphans_are_errors(sandbox, manifest):
    with sqlite3.connect(sandbox.db, autocommit=True) as w:
        w.execute("DELETE FROM library WHERE id = ?", (manifest["hotcue_track"],))
    findings = sandbox.library().doctor()
    orphans = by_check(findings, "orphans")
    assert {f.details["relation"] for f in orphans} >= {"cues.track_id -> library.id", "crate_tracks.track_id -> library.id"}
    assert all(f.severity == "error" for f in orphans)
    assert summarize(findings)["errors"] >= 2
    sandbox.__dict__["_skip_doctor"] = True  # this test corrupted the database on purpose


def test_schema_above_ceiling_is_an_error_but_doctor_still_runs(sandbox):
    with sqlite3.connect(sandbox.db, autocommit=True) as w:
        w.execute("UPDATE settings SET value = '41' WHERE name = 'mixxx.schema.version'")
    findings = sandbox.library().doctor()
    schema = by_check(findings, "schema")[0]
    assert schema.severity == "error" and schema.details["version"] == 41
    with sqlite3.connect(sandbox.db, autocommit=True) as w:
        w.execute("UPDATE settings SET value = '39' WHERE name = 'mixxx.schema.version'")


def test_missing_file_is_a_warning(sandbox, manifest):
    track_id = manifest["tracks"][3]
    (sandbox.audio / manifest["audio"][str(track_id)]).unlink()
    findings = sandbox.library().doctor()
    missing = by_check(findings, "missing_files")
    assert missing and missing[0].severity == "warning" and track_id in missing[0].details["track_ids"]


def test_duplicate_sibling_names_are_a_warning_at_42(sandbox_42, manifest_42):
    findings = sandbox_42.library().doctor()
    crates = by_check(findings, "crates")
    assert len(crates) == 1 and crates[0].details["duplicates"] == [{"name": "Dupe", "parent_id": None, "count": 2}]
    assert summarize(findings)["errors"] == 0


def test_cycle_is_an_error_at_42(sandbox_42, manifest_42):
    c = manifest_42["crates"]
    with sqlite3.connect(sandbox_42.db, autocommit=True) as w:
        w.execute("UPDATE crates SET parent_id = ? WHERE id = ?", (c["dubstep"], c["genres"]))
    findings = sandbox_42.library().doctor()
    cycles = [f for f in by_check(findings, "orphans") if f.details.get("relation") == "crates.parent_id cycle"]
    assert cycles and cycles[0].severity == "error"
    sandbox_42.__dict__["_skip_doctor"] = True


def test_outside_track_ignores_the_unset_sentinel_but_still_flags_the_injected_anomaly(sandbox):
    # Mixxx writes -1 as "not set" for a cue's position; the fixture carries 19 such sentinel cues
    # (type 7 / outro). None of them is a real anomaly and none must be reported.
    sentinel_ids = {row[0] for row in sandbox.query("SELECT id FROM cues WHERE position = -1")}
    assert len(sentinel_ids) == 19
    findings = sandbox.library().doctor()
    outside = [f for f in by_check(findings, "cues") if f.details["reason"] == "outside_track"]
    assert len(outside) == 1
    reported_ids = set(outside[0].details["cue_ids"])
    assert not reported_ids & sentinel_ids
    # Task 6's deliberately-injected negative-position loop cue on track 818 must still be caught.
    assert reported_ids == {4242}
