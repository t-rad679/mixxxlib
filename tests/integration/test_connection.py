import sqlite3
from datetime import datetime, timezone

import pytest

from mixxxlib.db import connection as c
from mixxxlib.errors import DatabaseError, MixxxRunningError, NotFoundError, SchemaUnsupportedError, TagError

NOW = datetime(2026, 10, 8, 14, 22, 33, tzinfo=timezone.utc)
NEVER_RUNNING = lambda: False  # noqa: E731


def _rename_plan(new_name, crate_id):
    def build(conn):
        return c.MutationPlan(
            statements=[c.Statement("UPDATE crates SET name = ? WHERE id = ?", (new_name, crate_id))],
            data={"crate_id": crate_id},
            after_commit=lambda conn: {"name": conn.execute("SELECT name FROM crates WHERE id = ?", (crate_id,)).fetchone()[0]},
        )
    return build


def test_open_ro_refuses_writes_and_missing_paths(sandbox, tmp_path):
    conn = c.open_ro(sandbox.db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO crates (name) VALUES ('x')")
    with pytest.raises(NotFoundError) as e:
        c.open_ro(tmp_path / "nope.sqlite")
    assert e.value.details["path"].endswith("nope.sqlite")


def test_render_statement_binds_parameters():
    st = c.Statement("INSERT INTO cues (track_id, label, position, color) VALUES (?, ?, ?, ?)", (7, "it's", 1.5, None))
    assert c.render_statement(st) == "INSERT INTO cues (track_id, label, position, color) VALUES (7, 'it''s', 1.5, NULL)"
    assert c.render_statement(c.Statement("UPDATE library SET source_synchronized_ms = ?", (c.Deferred("mtime_ms"),))) == \
        "UPDATE library SET source_synchronized_ms = <mtime_ms>"


def test_dry_run_changes_nothing_and_takes_no_backup(sandbox, manifest):
    crate_id = manifest["crates"]["loops"]
    conn = c.open_rw(sandbox.db)
    result = c.run_mutation(conn, label="crate rename", backup_dir=sandbox.backup_dir, build=_rename_plan("Renamed", crate_id),
                            schema_ceiling=40, dry_run=True, process_check=NEVER_RUNNING)
    assert result.dry_run is True and result.backup is None and result.integrity is None
    assert result.statements == [f"UPDATE crates SET name = 'Renamed' WHERE id = {crate_id}"]
    assert sandbox.scalar("SELECT name FROM crates WHERE id = ?", crate_id) == "Fixture Loops"
    assert not sandbox.backup_dir.exists()


def test_successful_mutation_backs_up_commits_and_verifies(sandbox, manifest):
    crate_id = manifest["crates"]["loops"]
    conn = c.open_rw(sandbox.db)
    result = c.run_mutation(conn, label="crate rename", backup_dir=sandbox.backup_dir, build=_rename_plan("Renamed", crate_id),
                            schema_ceiling=40, process_check=NEVER_RUNNING, now=NOW)
    assert result.backup == str(sandbox.backup_dir / "mixxxdb-20261008T142233Z-crate-rename.sqlite")
    assert result.integrity == "ok" and result.dry_run is False
    assert result.data == {"name": "Renamed"}
    assert sandbox.scalar("SELECT name FROM crates WHERE id = ?", crate_id) == "Renamed"
    backup = sqlite3.connect(result.backup)
    assert backup.execute("SELECT name FROM crates WHERE id = ?", (crate_id,)).fetchone()[0] == "Fixture Loops"


def test_failed_statement_rolls_back_and_names_the_backup(sandbox, manifest):
    crate_id = manifest["crates"]["loops"]

    def build(conn):
        return c.MutationPlan(statements=[
            c.Statement("UPDATE crates SET name = ? WHERE id = ?", ("Half", crate_id)),
            c.Statement("INSERT INTO no_such_table VALUES (1)"),
        ])

    conn = c.open_rw(sandbox.db)
    with pytest.raises(DatabaseError) as e:
        c.run_mutation(conn, label="x", backup_dir=sandbox.backup_dir, build=build, schema_ceiling=40, process_check=NEVER_RUNNING)
    assert e.value.details["backup"].endswith(".sqlite")
    assert sandbox.scalar("SELECT name FROM crates WHERE id = ?", crate_id) == "Fixture Loops"
    assert not conn.in_transaction


def test_validation_errors_from_build_happen_before_any_backup(sandbox):
    def build(conn):
        raise NotFoundError("crate 999 does not exist", crate_id=999)

    conn = c.open_rw(sandbox.db)
    with pytest.raises(NotFoundError):
        c.run_mutation(conn, label="x", backup_dir=sandbox.backup_dir, build=build, schema_ceiling=40, process_check=NEVER_RUNNING)
    assert not sandbox.backup_dir.exists() and not conn.in_transaction


def test_lock_and_process_guards(sandbox, manifest):
    conn = c.open_rw(sandbox.db)
    with pytest.raises(MixxxRunningError):
        c.run_mutation(conn, label="x", backup_dir=sandbox.backup_dir, build=_rename_plan("R", 1), schema_ceiling=40, process_check=lambda: True)
    holder = sqlite3.connect(sandbox.db, autocommit=True)
    holder.execute("BEGIN EXCLUSIVE")
    with pytest.raises(MixxxRunningError):
        c.run_mutation(conn, label="x", backup_dir=sandbox.backup_dir, build=_rename_plan("R", 1), schema_ceiling=40, process_check=NEVER_RUNNING)
    holder.execute("ROLLBACK")
    assert not sandbox.backup_dir.exists()


def test_schema_guard_refuses_41_on_ceiling_40(sandbox, manifest):
    with sqlite3.connect(sandbox.db, autocommit=True) as w:
        w.execute("UPDATE settings SET value = '41' WHERE name = 'mixxx.schema.version'")
    conn = c.open_rw(sandbox.db)
    with pytest.raises(SchemaUnsupportedError) as e:
        c.run_mutation(conn, label="x", backup_dir=sandbox.backup_dir, build=_rename_plan("R", 1), schema_ceiling=40, process_check=NEVER_RUNNING)
    assert e.value.details == {"version": 41, "ceiling": 40}
    with sqlite3.connect(sandbox.db, autocommit=True) as w:
        w.execute("UPDATE settings SET value = '39' WHERE name = 'mixxx.schema.version'")


class RecordingAction:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.applied, self.rolled_back = name, fail, False, False

    def describe(self):
        return f"write {self.name}"

    def apply(self):
        if self.fail:
            raise TagError(f"{self.name} exploded")
        self.applied = True
        return {"mtime_ms": 1234}

    def rollback(self):
        self.rolled_back = True


def test_file_actions_run_before_the_transaction_and_feed_deferred_params(sandbox, manifest):
    track_id = manifest["tracks"][0]
    action = RecordingAction("a.aiff")

    def build(conn):
        return c.MutationPlan(
            statements=[c.Statement("UPDATE library SET source_synchronized_ms = ? WHERE id = ?", (c.Deferred("mtime_ms"), track_id))],
            file_actions=[action],
        )

    conn = c.open_rw(sandbox.db)
    result = c.run_mutation(conn, label="track set", backup_dir=sandbox.backup_dir, build=build, schema_ceiling=40, process_check=NEVER_RUNNING)
    assert action.applied and result.file_operations == ["write a.aiff"]
    assert sandbox.scalar("SELECT source_synchronized_ms FROM library WHERE id = ?", track_id) == 1234


def test_failed_file_action_rolls_back_earlier_actions_and_skips_the_database(sandbox, manifest):
    first, second = RecordingAction("one"), RecordingAction("two", fail=True)

    def build(conn):
        return c.MutationPlan(statements=[c.Statement("UPDATE crates SET name = 'X' WHERE id = ?", (manifest["crates"]["loops"],))],
                              file_actions=[first, second])

    conn = c.open_rw(sandbox.db)
    with pytest.raises(TagError):
        c.run_mutation(conn, label="track set", backup_dir=sandbox.backup_dir, build=build, schema_ceiling=40, process_check=NEVER_RUNNING)
    assert first.rolled_back and not second.applied
    assert sandbox.scalar("SELECT name FROM crates WHERE id = ?", manifest["crates"]["loops"]) == "Fixture Loops"


def test_database_failure_after_file_write_restores_the_file(sandbox, manifest):
    action = RecordingAction("one")

    def build(conn):
        return c.MutationPlan(statements=[c.Statement("INSERT INTO no_such_table VALUES (1)")], file_actions=[action])

    conn = c.open_rw(sandbox.db)
    with pytest.raises(DatabaseError) as e:
        c.run_mutation(conn, label="track set", backup_dir=sandbox.backup_dir, build=build, schema_ceiling=40, process_check=NEVER_RUNNING)
    assert action.rolled_back
    assert e.value.details["file_rollback"] == ["write one"]
