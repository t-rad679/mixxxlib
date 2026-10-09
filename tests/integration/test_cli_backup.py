import os
import time


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
