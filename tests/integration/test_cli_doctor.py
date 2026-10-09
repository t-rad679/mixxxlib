import sqlite3


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
