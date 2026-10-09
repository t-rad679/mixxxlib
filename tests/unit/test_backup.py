import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mixxxlib.errors import ValidationError
from mixxxlib.safety import backup

NOW = datetime(2026, 10, 8, 14, 22, 33, tzinfo=timezone.utc)


def test_backup_name_uses_utc_basic_timestamp_and_hyphenated_label():
    assert backup.backup_name("crate add", NOW) == "mixxxdb-20261008T142233Z-crate-add.sqlite"
    assert backup.backup_name("manual label!", NOW) == "mixxxdb-20261008T142233Z-manual-label.sqlite"


def test_retention_from_env_and_defaults():
    assert backup.Retention.from_env({}) == backup.Retention(30, 10)
    assert backup.Retention.from_env({"MIXXXLIB_BACKUP_TTL_DAYS": "7", "MIXXXLIB_BACKUP_KEEP_MIN": "3"}) == backup.Retention(7, 3)
    with pytest.raises(ValidationError):
        backup.Retention.from_env({"MIXXXLIB_BACKUP_TTL_DAYS": "soon"})


def _touch(path: Path, when: datetime) -> None:
    path.write_bytes(b"x")
    os.utime(path, (when.timestamp(), when.timestamp()))


def test_prune_keeps_the_floor_even_when_everything_is_stale(tmp_path):
    for i in range(12):
        _touch(tmp_path / f"mixxxdb-2026010{i % 10}T00000{i}Z-x.sqlite", NOW - timedelta(days=100 + i))
    removed = backup.prune(tmp_path, backup.Retention(ttl_days=30, keep_min=10), now=NOW)
    assert len(removed) == 2
    assert len(list(tmp_path.glob("mixxxdb-*.sqlite"))) == 10
    assert all("00000" in p.name for p in removed)  # the two oldest by mtime went


def test_prune_keeps_recent_files_beyond_the_floor(tmp_path):
    for i in range(15):
        _touch(tmp_path / f"mixxxdb-20261001T0000{i:02d}Z-x.sqlite", NOW - timedelta(days=i))
    removed = backup.prune(tmp_path, backup.Retention(ttl_days=30, keep_min=10), now=NOW)
    assert removed == []


def test_prune_treats_tag_snapshots_as_their_own_series(tmp_path):
    for i in range(11):
        _touch(tmp_path / f"tags-20260101T0000{i:02d}Z-7.json", NOW - timedelta(days=200 + i))
    _touch(tmp_path / "mixxxdb-20260101T000000Z-x.sqlite", NOW - timedelta(days=200))
    removed = backup.prune(tmp_path, backup.Retention(30, 10), now=NOW)
    assert [p.suffix for p in removed] == [".json"]
    assert (tmp_path / "mixxxdb-20260101T000000Z-x.sqlite").exists()


def test_create_backup_is_a_consistent_copy(tmp_path):
    db = tmp_path / "db.sqlite"
    conn = sqlite3.connect(db, autocommit=True)
    conn.execute("CREATE TABLE t (x)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(100)])
    path = backup.create_backup(conn, tmp_path / "backups", "crate add", now=NOW)
    assert path == tmp_path / "backups" / "mixxxdb-20261008T142233Z-crate-add.sqlite"
    copy = sqlite3.connect(path)
    assert copy.execute("SELECT count(*) FROM t").fetchone()[0] == 100
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    second = backup.create_backup(conn, tmp_path / "backups", "crate add", now=NOW)
    assert second.name == "mixxxdb-20261008T142233Z-crate-add-2.sqlite"


def test_list_backups_newest_first_with_kind_and_age(tmp_path):
    _touch(tmp_path / "mixxxdb-20261001T000000Z-a.sqlite", NOW - timedelta(days=7))
    _touch(tmp_path / "tags-20261007T000000Z-9.json", NOW - timedelta(days=1))
    entries = backup.list_backups(tmp_path, now=NOW)
    assert [e.kind for e in entries] == ["tags", "database"]
    assert entries[0].age_seconds == 86400
    assert entries[1].size == 1
    assert backup.list_backups(tmp_path / "missing") == []
