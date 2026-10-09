"""VACUUM INTO backups, naming and retention (spec §7.6)."""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mixxxlib.errors import DatabaseError, ValidationError
from mixxxlib.model import BackupEntry

DB_PREFIX = "mixxxdb-"
TAG_PREFIX = "tags-"


@dataclass(frozen=True)
class Retention:
    ttl_days: int = 30
    keep_min: int = 10

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Retention":
        env = os.environ if environ is None else environ
        values = {}
        for attr, var in (("ttl_days", "MIXXXLIB_BACKUP_TTL_DAYS"), ("keep_min", "MIXXXLIB_BACKUP_KEEP_MIN")):
            raw = env.get(var)
            if raw is None:
                continue
            try:
                values[attr] = int(raw)
            except ValueError:
                raise ValidationError(f"{var} must be an integer, got {raw!r}", variable=var, value=raw) from None
            if values[attr] < 0:
                raise ValidationError(f"{var} must be non-negative", variable=var, value=raw)
        return cls(**values)


def utc_stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._]+", "-", label.strip()).strip("-") or "backup"


def backup_name(label: str, now: datetime) -> str:
    return f"{DB_PREFIX}{utc_stamp(now)}-{_slug(label)}.sqlite"


def _unique(path: Path) -> Path:
    candidate, n = path, 1
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
    return candidate


def create_backup(conn: sqlite3.Connection, backup_dir: Path, label: str, *, now: datetime | None = None) -> Path:
    """Copy the open database with VACUUM INTO (never cp: a copied file can hold a torn page set)."""
    stamp = now or datetime.now(timezone.utc)
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DatabaseError(f"cannot create backup directory {backup_dir}: {exc}", backup_dir=str(backup_dir)) from exc
    target = _unique(backup_dir / backup_name(label, stamp))
    literal = str(target).replace("'", "''")
    try:
        conn.execute(f"VACUUM INTO '{literal}'")
    except sqlite3.Error as exc:
        raise DatabaseError(f"backup failed: {exc}", backup=str(target)) from exc
    return target


def _entries(backup_dir: Path) -> list[tuple[Path, str, float]]:
    if not backup_dir.is_dir():
        return []
    found = []
    for path in backup_dir.iterdir():
        if path.name.startswith(DB_PREFIX) and path.suffix == ".sqlite":
            found.append((path, "database", path.stat().st_mtime))
        elif path.name.startswith(TAG_PREFIX) and path.suffix == ".json":
            found.append((path, "tags", path.stat().st_mtime))
    found.sort(key=lambda e: (-e[2], e[0].name))
    return found


def list_backups(backup_dir: Path, *, now: datetime | None = None) -> list[BackupEntry]:
    current = (now or datetime.now(timezone.utc)).timestamp()
    return [
        BackupEntry(
            path=str(path), name=path.name, kind=kind, size=path.stat().st_size,
            modified=datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            age_seconds=int(current - mtime),
        )
        for path, kind, mtime in _entries(backup_dir)
    ]


def prune(backup_dir: Path, retention: Retention, *, now: datetime | None = None) -> list[Path]:
    """Delete entries older than ttl_days, but always keep the keep_min newest of each kind."""
    cutoff = (now or datetime.now(timezone.utc)).timestamp() - retention.ttl_days * 86400
    removed: list[Path] = []
    for kind in ("database", "tags"):
        series = [e for e in _entries(backup_dir) if e[1] == kind]
        for path, _, mtime in series[retention.keep_min:]:
            if mtime < cutoff:
                path.unlink()
                removed.append(path)
    return removed
