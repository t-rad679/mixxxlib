import os
import sqlite3
import time
from contextlib import closing


def _candidate_with_schema_version(sandbox, tmp_path, name, version) -> str:
    """A legit backup, copied aside and with its declared mixxx.schema.version row overwritten.

    The DDL itself is untouched (still structurally revision 39, like the fixture) — only the
    *declared* version row changes, which is exactly the scenario schema_info() has to classify:
    a database that claims a revision it may or may not actually match.
    """
    good = sandbox.run("backup", "create", "--json").json["data"]["backup"]
    candidate = tmp_path / name
    candidate.write_bytes(open(good, "rb").read())
    with closing(sqlite3.connect(candidate, autocommit=True)) as conn:
        conn.execute("UPDATE settings SET value = ? WHERE name = 'mixxx.schema.version'", (str(version),))
    return str(candidate)


def test_backup_create_list_prune(sandbox, monkeypatch):
    r = sandbox.run("backup", "create", "--json")
    assert r.code == 0
    first = r.json["data"]["backup"]
    assert first.startswith(str(sandbox.backup_dir / "mixxxdb-")) and first.endswith("-backup-create.sqlite")
    r = sandbox.run("backup", "create", "--label", "before import", "--json")
    second = r.json["data"]["backup"]
    assert second.endswith("-before-import.sqlite") and r.json["data"]["size"] > 0
    r = sandbox.run("backup", "list", "--json")
    names = [b["name"] for b in r.json["data"]["backups"]]
    assert len(names) == 2 and all(b["kind"] == "database" for b in r.json["data"]["backups"])
    r = sandbox.run("backup", "list")
    assert r.code == 0 and "before-import" in r.stdout
    old = time.time() - 90 * 86400
    os.utime(first, (old, old))
    monkeypatch.setenv("MIXXXLIB_BACKUP_KEEP_MIN", "1")
    r = sandbox.run("backup", "prune", "--json")
    assert r.json["data"]["removed"] == [first]
    assert not os.path.exists(first) and os.path.exists(second)


def test_backup_restore(sandbox, manifest):
    original = sandbox.scalar("SELECT name FROM crates WHERE id = ?", manifest["crates"]["loops"])
    backup = sandbox.run("backup", "create", "--json").json["data"]["backup"]
    sandbox.run("crate", "rename", "Fixture Loops", "Changed")
    assert sandbox.scalar("SELECT name FROM crates WHERE id = ?", manifest["crates"]["loops"]) == "Changed"
    r = sandbox.run("backup", "restore", backup, "--json")
    assert r.code == 8
    r = sandbox.run("backup", "restore", backup, "--yes", "--json")
    assert r.code == 0
    data = r.json["data"]
    assert data["restored_from"] == backup and data["integrity"] == "ok"
    assert data["backup"].endswith("-backup-restore.sqlite") and os.path.exists(data["backup"])
    assert sandbox.scalar("SELECT name FROM crates WHERE id = ?", manifest["crates"]["loops"]) == original


def test_backup_restore_refuses_bad_candidates(sandbox, tmp_path):
    bogus = tmp_path / "bogus.sqlite"
    bogus.write_bytes(b"not a database at all" * 100)
    r = sandbox.run("backup", "restore", str(bogus), "--yes", "--json")
    assert r.code == 7 and r.error["error"]["code"] == "database"
    r = sandbox.run("backup", "restore", str(tmp_path / "missing.sqlite"), "--yes", "--json")
    assert r.code == 3


def test_backup_restore_missing_file_does_not_require_yes(sandbox, tmp_path):
    """A nonexistent backup file is exit 3 even without --yes (resolve happens before the confirmation gate)."""
    r = sandbox.run("backup", "restore", str(tmp_path / "missing.sqlite"), "--json")
    assert r.code == 3 and r.error["error"]["code"] == "not_found"


def test_backup_restore_existing_file_without_yes_is_8(sandbox):
    backup = sandbox.run("backup", "create", "--json").json["data"]["backup"]
    r = sandbox.run("backup", "restore", backup, "--json")
    assert r.code == 8 and r.error["error"]["code"] == "confirmation_required"


def test_backup_restore_truncated_candidate_leaves_live_db_untouched(sandbox, manifest, tmp_path):
    """A truncated (but real-sqlite-header) backup must be refused by PRAGMA integrity_check
    before the live database is touched at all — the single worst failure mode this tool could have."""
    original_name = sandbox.scalar("SELECT name FROM crates WHERE id = ?", manifest["crates"]["loops"])
    original_bytes = sandbox.db.read_bytes()

    good = sandbox.run("backup", "create", "--json").json["data"]["backup"]
    truncated = tmp_path / "truncated.sqlite"
    full = open(good, "rb").read()
    truncated.write_bytes(full[: len(full) // 2])

    r = sandbox.run("backup", "restore", str(truncated), "--yes", "--json")
    assert r.code == 7 and r.error["error"]["code"] == "database"

    # The live database must be byte-for-byte untouched: no backup of it was taken, and no swap happened.
    assert sandbox.db.read_bytes() == original_bytes
    assert sandbox.scalar("SELECT name FROM crates WHERE id = ?", manifest["crates"]["loops"]) == original_name
    # No stray "backup restore" safety copy should have been made, since we never got that far.
    r = sandbox.run("backup", "list", "--json")
    labels = [b["name"] for b in r.json["data"]["backups"]]
    assert not any("backup-restore" in name for name in labels)


def test_backup_restore_refuses_candidate_above_ceiling(sandbox, manifest, tmp_path):
    """A candidate declaring a schema revision above this library's ceiling (40) must be refused
    before anything about the live database is touched — restoring it would install a database
    that `doctor` immediately flags as an error-severity finding (schema above the ceiling)."""
    original_name = sandbox.scalar("SELECT name FROM crates WHERE id = ?", manifest["crates"]["loops"])
    original_bytes = sandbox.db.read_bytes()
    candidate = _candidate_with_schema_version(sandbox, tmp_path, "above-ceiling.sqlite", 999)

    r = sandbox.run("backup", "restore", candidate, "--yes", "--json")
    assert r.code == 6 and r.error["error"]["code"] == "schema_unsupported"

    assert sandbox.db.read_bytes() == original_bytes
    assert sandbox.scalar("SELECT name FROM crates WHERE id = ?", manifest["crates"]["loops"]) == original_name
    labels = [b["name"] for b in sandbox.run("backup", "list", "--json").json["data"]["backups"]]
    assert not any("backup-restore" in name for name in labels)


def test_backup_restore_refuses_candidate_below_floor(sandbox, manifest, tmp_path):
    """A candidate declaring a schema revision below the lowest known revision (39) must also be
    refused before the live database is touched."""
    original_name = sandbox.scalar("SELECT name FROM crates WHERE id = ?", manifest["crates"]["loops"])
    original_bytes = sandbox.db.read_bytes()
    candidate = _candidate_with_schema_version(sandbox, tmp_path, "below-floor.sqlite", 5)

    r = sandbox.run("backup", "restore", candidate, "--yes", "--json")
    assert r.code == 6 and r.error["error"]["code"] == "schema_unsupported"

    assert sandbox.db.read_bytes() == original_bytes
    assert sandbox.scalar("SELECT name FROM crates WHERE id = ?", manifest["crates"]["loops"]) == original_name
    labels = [b["name"] for b in sandbox.run("backup", "list", "--json").json["data"]["backups"]]
    assert not any("backup-restore" in name for name in labels)


def test_backup_restore_refuses_candidate_with_missing_schema_row(sandbox, tmp_path):
    """A candidate with no mixxx.schema.version row at all is also refused as schema_unsupported
    (schema_info()'s "missing" verdict), not as a generic database error."""
    good = sandbox.run("backup", "create", "--json").json["data"]["backup"]
    candidate = tmp_path / "no-version-row.sqlite"
    candidate.write_bytes(open(good, "rb").read())
    with closing(sqlite3.connect(candidate, autocommit=True)) as conn:
        conn.execute("DELETE FROM settings WHERE name = 'mixxx.schema.version'")

    r = sandbox.run("backup", "restore", str(candidate), "--yes", "--json")
    assert r.code == 6 and r.error["error"]["code"] == "schema_unsupported"


def test_backup_restore_allows_unverified_candidate(sandbox, manifest, tmp_path):
    """A candidate whose declared revision is in range but not in VERIFIED_REVISIONS (e.g. 40,
    between the floor and ceiling but never exercised against a real database) is "unverified",
    not "unsupported" — restore must still proceed for it, same as every other mutating command."""
    candidate = _candidate_with_schema_version(sandbox, tmp_path, "unverified.sqlite", 40)

    r = sandbox.run("backup", "restore", candidate, "--yes", "--json")
    assert r.code == 0
    assert r.json["data"]["integrity"] == "ok"
