import os
import sqlite3

from tests.conftest import run_cli


def test_doctor_json_clean_fixture(sandbox):
    r = sandbox.run("doctor", "--json")
    assert r.code == 0
    data = r.json["data"]
    assert set(data) == {"findings", "summary"}
    assert data["summary"]["errors"] == 0 and data["summary"]["warnings"] >= 2
    assert set(data["findings"][0]) == {"check", "severity", "message", "details"}


def test_doctor_text_and_unhealthy_exit(sandbox, manifest):
    r = sandbox.run("doctor")
    assert r.code == 0 and "[warning] cues:" in r.stdout and "errors: 0" in r.stdout
    with sqlite3.connect(sandbox.db, autocommit=True) as w:
        w.execute("DELETE FROM track_locations WHERE id = (SELECT location FROM library WHERE id = ?)", (manifest["tracks"][0],))
    r = sandbox.run("doctor", "--json")
    assert r.code == 10 and r.json["ok"] is True and r.json["data"]["summary"]["errors"] >= 1
    sandbox.__dict__["_skip_doctor"] = True


def test_doctor_runs_above_the_ceiling(sandbox):
    with sqlite3.connect(sandbox.db, autocommit=True) as w:
        w.execute("UPDATE settings SET value = '41' WHERE name = 'mixxx.schema.version'")
    r = sandbox.run("doctor", "--json")
    assert r.code == 10 and r.json["data"]["findings"][0]["check"] == "schema"
    with sqlite3.connect(sandbox.db, autocommit=True) as w:
        w.execute("UPDATE settings SET value = '39' WHERE name = 'mixxx.schema.version'")


def test_doctor_exit_0_on_clean_sandbox(sandbox):
    r = sandbox.run("doctor", "--json")
    assert r.code == 0 and r.json["data"]["summary"]["errors"] == 0


def test_doctor_exit_10_on_orphan_row(sandbox, manifest):
    with sqlite3.connect(sandbox.db, autocommit=True) as w:
        w.execute("DELETE FROM crates WHERE id = ?", (manifest["crates"]["loops"],))
    r = sandbox.run("doctor", "--json")
    assert r.code == 10
    data = r.json["data"]
    assert data["summary"]["errors"] >= 1
    orphan_findings = [f for f in data["findings"] if f["check"] == "orphans" and f["severity"] == "error"]
    assert orphan_findings
    sandbox.__dict__["_skip_doctor"] = True


def test_doctor_exit_10_is_not_triggered_by_warnings_alone(sandbox):
    """The clean fixture has warning-severity findings (e.g. cues) but zero errors: exit must be 0."""
    r = sandbox.run("doctor", "--json")
    assert r.code == 0
    data = r.json["data"]
    assert data["summary"]["warnings"] > 0
    assert data["summary"]["errors"] == 0
    assert all(f["severity"] != "error" for f in data["findings"])


def test_a_raising_check_degrades_to_an_error_finding_without_discarding_the_others(sandbox):
    """Final-review item 3: run_doctor had no per-check error isolation, so one check raising
    (e.g. a dropped/renamed table) unwound the whole report, discarding findings earlier checks
    had already produced -- including, in the worst case, check_schema's own diagnosis of why the
    database is broken in the first place. Each check must now fail in isolation."""
    with sqlite3.connect(sandbox.db, autocommit=True) as w:
        w.execute("DROP TABLE track_analysis")
    r = sandbox.run("doctor", "--json")
    assert r.code == 10 and r.json["ok"] is True
    data = r.json["data"]
    checks = {f["check"] for f in data["findings"]}
    # earlier checks' findings (schema, mixxx_running, integrity) survived check_orphans raising
    assert {"schema", "mixxx_running", "integrity"} <= checks
    broken = [f for f in data["findings"] if f["check"] == "orphans" and f["severity"] == "error"]
    assert broken and "track_analysis" in broken[0]["message"]
    # later checks also still ran and contributed their own findings
    assert any(f["check"] == "backups" for f in data["findings"])
    sandbox.__dict__["_skip_doctor"] = True


def test_non_numeric_key_id_is_a_degraded_finding_not_an_internal_crash(sandbox, manifest):
    """SQLite's dynamic typing lets `key_id` (nominally INTEGER) hold non-numeric text. check_keys'
    unguarded int(key_id or 0) used to raise ValueError and take down the entire doctor report
    with exit 1 ("mixxxlib has a bug") instead of reporting the row as an ordinary `keys` finding
    ("your database has bad data")."""
    track_id = manifest["tracks"][0]
    with sqlite3.connect(sandbox.db, autocommit=True) as w:
        w.execute("UPDATE library SET key_id = 'abc' WHERE id = ?", (track_id,))
    r = sandbox.run("doctor", "--json")
    # "keys" findings are warning-severity (same as the fixture's pre-existing key mismatch), so
    # this must come back exit 0, not crash with exit 1 as a bare ValueError.
    assert r.code == 0, r.stderr
    keys = [f for f in r.json["data"]["findings"] if f["check"] == "keys"]
    assert keys and track_id in keys[0]["details"]["track_ids"]
    sandbox.__dict__["_skip_doctor"] = True


def test_doctor_on_a_non_sqlite_file_degrades_instead_of_crashing(tmp_path, clean_env):
    """The most informative repro for item 3: a file that isn't a SQLite database at all.
    Previously this discarded even check_schema's own correct "file is not a database" verdict --
    thrown away two checks later when check_integrity raised -- and printed nothing to stdout
    (exit 7, empty body). Explicit --backup-dir keeps this test off the real default backup dir."""
    garbage = tmp_path / "garbage.sqlite"
    garbage.write_bytes(os.urandom(2048))
    r = run_cli("--db", str(garbage), "--backup-dir", str(tmp_path / "backups"), "doctor", "--json")
    assert r.code == 10 and r.json["ok"] is True
    findings = r.json["data"]["findings"]
    schema = next(f for f in findings if f["check"] == "schema")
    assert schema["severity"] == "error" and "not a database" in schema["message"]
    assert any(f["check"] == "mixxx_running" for f in findings)  # unrelated to the db; still ran
    assert any(f["check"] == "integrity" and f["severity"] == "error" for f in findings)
    assert any(f["check"] == "backups" for f in findings)  # later checks still ran too
