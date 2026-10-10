"""The public API façade (spec §10.1). Never imports Typer."""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from mixxxlib import __version__, doctor as doctor_mod, paths as paths_mod
from mixxxlib.db import connection as conn_mod, crates as crates_sql, schema as schema_mod, tracks as tracks_sql
from mixxxlib.errors import DatabaseError, NotFoundError, SchemaUnsupportedError, ValidationError
from mixxxlib.model import BackupEntry, Crate, CrateNode, Finding, MutationResult, SchemaInfo, Track, WarningMessage
from mixxxlib.safety import backup as backup_mod
from mixxxlib.safety.guard import assert_mixxx_closed, mixxx_process_running
from mixxxlib.settings import load_mixxx_cfg


class Library:
    def __init__(self, resolved: paths_mod.ResolvedPaths, *, schema_ceiling: int,
                 environ: Mapping[str, str] | None = None,
                 process_check: Callable[[], bool | None] | None = None) -> None:
        self.paths = resolved
        self.schema_ceiling = schema_ceiling
        self._environ = os.environ if environ is None else environ
        self._process_check = process_check or mixxx_process_running
        self.retention = backup_mod.Retention.from_env(self._environ)
        with closing(conn_mod.open_ro(resolved.db)) as conn, conn_mod.translating_errors():
            self.schema: SchemaInfo = schema_mod.schema_info(conn, ceiling=schema_ceiling)
        self.crates = CratesAPI(self)
        self.tracks = TracksAPI(self)
        self.backups = BackupsAPI(self)

    @classmethod
    def open(cls, settings_path: str | None = None, db: str | None = None, backup_dir: str | None = None, *,
             schema_ceiling: int | None = None, environ: Mapping[str, str] | None = None,
             process_check: Callable[[], bool | None] | None = None) -> "Library":
        resolved = paths_mod.resolve(settings_path, db, backup_dir, environ=environ)
        ceiling = schema_mod.SCHEMA_CEILING if schema_ceiling is None else schema_ceiling
        return cls(resolved, schema_ceiling=ceiling, environ=environ, process_check=process_check)

    # -- schema / state -------------------------------------------------------------

    @property
    def nested(self) -> bool:
        return "nested_crates" in self.schema.capabilities

    def require_nested(self) -> None:
        schema_mod.require_schema(self.schema, capability="nested_crates")

    def mixxx_running(self) -> bool | None:
        return self._process_check()

    def read_warnings(self) -> list[WarningMessage]:
        if self.mixxx_running() is True:
            return [WarningMessage("mixxx_running", "Mixxx is running; values may be stale", {})]
        return []

    # -- connections ----------------------------------------------------------------

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with closing(conn_mod.open_ro(self.paths.db)) as conn, conn_mod.translating_errors():
            yield conn

    def mutate(self, label: str, build: Callable[[sqlite3.Connection], conn_mod.MutationPlan], *,
               dry_run: bool = False, requires: str | None = None) -> MutationResult:
        with closing(conn_mod.open_rw(self.paths.db)) as conn:
            return conn_mod.run_mutation(
                conn, label=label, backup_dir=self.paths.backup_dir, build=build, schema_ceiling=self.schema_ceiling,
                requires=requires, dry_run=dry_run, retention=self.retention, process_check=self._process_check,
            )

    # -- settings / info ------------------------------------------------------------

    @property
    def settings(self) -> dict[str, dict[str, str]]:
        return load_mixxx_cfg(self.paths.config)

    def info(self) -> dict[str, Any]:
        backups = backup_mod.list_backups(self.paths.backup_dir)
        return {
            "version": __version__,
            "paths": {"settings_dir": str(self.paths.settings_dir), "db": str(self.paths.db),
                      "config": str(self.paths.config), "config_exists": self.paths.config.is_file(),
                      "backup_dir": str(self.paths.backup_dir)},
            "schema": {"version": self.schema.version, "ceiling": self.schema.ceiling,
                       "capabilities": sorted(self.schema.capabilities), "verdict": self.schema.verdict,
                       "message": self.schema.message},
            "mixxx_running": self.mixxx_running(),
            "backups": {"count": len(backups), "newest": backups[0].name if backups else None},
        }

    # -- doctor -----------------------------------------------------------------------

    def doctor(self) -> list[Finding]:
        with self.read() as conn:
            # self.nested trusts the declared schema version; a database whose settings row claims a
            # revision the DDL was never migrated to (e.g. schema bumped past the ceiling by hand) would
            # otherwise make orphans/crates checks query crates.parent_id, a column that does not exist.
            # Doctor must survive that — cross-check the actual column so it degrades instead of crashing.
            nested = self.nested and schema_mod.has_column(conn, "crates", "parent_id")
            ctx = doctor_mod.DoctorContext(conn=conn, schema=schema_mod.schema_info(conn, ceiling=self.schema_ceiling),
                                           backup_dir=self.paths.backup_dir, process_check=self._process_check,
                                           nested=nested)
            return doctor_mod.run_doctor(ctx)


class CratesAPI:
    def __init__(self, lib: Library) -> None:
        self._lib = lib

    def list(self, *, parent: Crate | None = None, roots_only: bool = False) -> list[Crate]:
        if parent is not None:
            self._lib.require_nested()
        with self._lib.read() as conn:
            return crates_sql.list_crates(conn, nested=self._lib.nested, parent_id=parent.id if parent else None,
                                          roots_only=roots_only)

    def get(self, selector: str | int, *, by_id: bool = False, parent: Crate | None = None) -> Crate:
        if parent is not None:
            self._lib.require_nested()
        with self._lib.read() as conn:
            if by_id:
                return crates_sql.get_crate_by_id(conn, int(selector), nested=self._lib.nested)
            return crates_sql.find_crate_by_name(conn, str(selector), nested=self._lib.nested,
                                                 parent_id=parent.id if parent else None, parent_given=parent is not None)

    def tree(self) -> list[CrateNode]:
        with self._lib.read() as conn:
            return crates_sql.tree(conn, nested=self._lib.nested)

    def subtree_ids(self, crate: Crate, *, recursive: bool) -> list[int]:
        if not recursive:
            return [crate.id]
        self._lib.require_nested()
        with self._lib.read() as conn:
            return [crate.id, *crates_sql.descendant_ids(conn, crate.id)]

    def members(self, crate: Crate, *, recursive: bool = False) -> list[int]:
        ids = self.subtree_ids(crate, recursive=recursive)
        with self._lib.read() as conn:
            found: set[int] = set()
            for cid in ids:
                found |= crates_sql.member_ids(conn, cid)
        return sorted(found)

    def create(self, name: str, *, parent: Crate | None = None, dry_run: bool = False) -> MutationResult:
        nested = self._lib.nested
        return self._lib.mutate("crate create", lambda c: crates_sql.plan_create(c, name, parent=parent, nested=nested),
                                dry_run=dry_run, requires="nested_crates" if parent is not None else None)

    def rename(self, crate: Crate, new_name: str, *, dry_run: bool = False) -> MutationResult:
        nested = self._lib.nested
        return self._lib.mutate("crate rename", lambda c: crates_sql.plan_rename(c, crate, new_name, nested=nested), dry_run=dry_run)

    def move(self, crate: Crate, *, parent: Crate | None, dry_run: bool = False) -> MutationResult:
        return self._lib.mutate("crate move", lambda c: crates_sql.plan_move(c, crate, parent=parent),
                                dry_run=dry_run, requires="nested_crates")

    def update(self, crate: Crate, *, locked: bool | None = None, show: bool | None = None,
               autodj_source: bool | None = None, dry_run: bool = False) -> MutationResult:
        nested = self._lib.nested
        return self._lib.mutate("crate update", lambda c: crates_sql.plan_update(
            c, crate, locked=locked, show=show, autodj_source=autodj_source, nested=nested), dry_run=dry_run)

    def delete(self, crate: Crate, *, recursive: bool = False, dry_run: bool = False) -> MutationResult:
        nested = self._lib.nested
        return self._lib.mutate("crate delete", lambda c: crates_sql.plan_delete(c, crate, recursive=recursive, nested=nested),
                                dry_run=dry_run)

    def add(self, crate: Crate, track_ids: Iterable[int], *, dry_run: bool = False) -> MutationResult:
        nested, ids = self._lib.nested, list(track_ids)
        return self._lib.mutate("crate add", lambda c: crates_sql.plan_add(c, crate, ids, nested=nested), dry_run=dry_run)

    def remove(self, crate: Crate, track_ids: Iterable[int], *, dry_run: bool = False) -> MutationResult:
        nested, ids = self._lib.nested, list(track_ids)
        return self._lib.mutate("crate remove", lambda c: crates_sql.plan_remove(c, crate, ids, nested=nested), dry_run=dry_run)

    def set_tracks(self, crate: Crate, track_ids: Iterable[int], *, dry_run: bool = False) -> MutationResult:
        nested, ids = self._lib.nested, list(track_ids)
        return self._lib.mutate("crate set-tracks", lambda c: crates_sql.plan_set_tracks(c, crate, ids, nested=nested), dry_run=dry_run)

    def export_m3u8(self, crate: Crate, *, recursive: bool = False) -> str:
        ids = self.subtree_ids(crate, recursive=recursive)
        with self._lib.read() as conn:
            return crates_sql.export_m3u8(conn, ids)


class TracksAPI:
    def __init__(self, lib: Library) -> None:
        self._lib = lib

    def _has_tuning(self, conn: sqlite3.Connection) -> bool:
        # Trusting the declared schema version alone is not enough: a fixture (or a database
        # whose schema row was bumped by hand) can claim a revision whose DDL it was never
        # migrated to. Cross-check the actual column, same defensive pattern as Library.doctor().
        return "tuning_frequency" in self._lib.schema.capabilities and schema_mod.has_column(conn, "library", "tuning_frequency_hz")

    def list(self, *, crate: Crate | None = None, search: str | None = None,
             recursive: bool = False) -> tuple[list[Track], list[WarningMessage]]:
        """Tracks sorted by id, plus decode warnings aggregated by code (see db.tracks._aggregate_warnings)."""
        crate_ids = self._lib.crates.subtree_ids(crate, recursive=recursive) if crate is not None else None
        with self._lib.read() as conn:
            return tracks_sql.list_tracks(conn, has_tuning=self._has_tuning(conn), crate_ids=crate_ids, search=search)

    def get(self, track_id: int) -> tuple[Track, list[WarningMessage]]:
        with self._lib.read() as conn:
            return tracks_sql.get_track(conn, int(track_id), has_tuning=self._has_tuning(conn))

    def get_by_path(self, path: str) -> tuple[Track, list[WarningMessage]]:
        with self._lib.read() as conn:
            return tracks_sql.get_track_by_path(conn, path, has_tuning=self._has_tuning(conn))

    def resolve(self, selectors: Iterable[str], *, paths: bool, missing_error=ValidationError) -> list[int]:
        with self._lib.read() as conn:
            return tracks_sql.resolve_track_ids(conn, selectors, paths=paths, missing_error=missing_error)


class BackupsAPI:
    def __init__(self, lib: Library) -> None:
        self._lib = lib

    def list(self) -> list[BackupEntry]:
        return backup_mod.list_backups(self._lib.paths.backup_dir)

    def create(self, label: str | None = None) -> Path:
        # Read-only: VACUUM INTO only reads the source connection, and a backup must never be able
        # to damage the database it is backing up. A write-capable handle here would be a gratuitous
        # risk for an operation that has no business writing to the live file at all.
        with closing(conn_mod.open_ro(self._lib.paths.db)) as conn, conn_mod.translating_errors():
            return backup_mod.create_backup(conn, self._lib.paths.backup_dir, label or "backup create")

    def prune(self) -> list[Path]:
        return backup_mod.prune(self._lib.paths.backup_dir, self._lib.retention)

    def restore(self, candidate: Path) -> dict[str, Any]:
        """Full mutation guard, back up the current database, verify the candidate, copy it into place.

        Order matters: the candidate is opened read-only and put through PRAGMA integrity_check and a
        full schema-revision check (against this library's configured ceiling, the same check every
        mutating command and `doctor` use) *before* anything about the live database is touched. Only
        once that has passed do we take a safety backup of the live database and then swap the
        candidate into place via a same-directory staging file + atomic os.replace, so a crash
        mid-copy can never leave a half-written mixxxdb.sqlite.
        """
        candidate = Path(candidate)
        if not candidate.is_file():
            raise NotFoundError(f"backup file not found at {candidate}", path=str(candidate))
        try:
            with closing(conn_mod.open_ro(candidate)) as check:
                result = conn_mod.integrity_check(check)
                # Read the version directly first so a missing `settings` table (not a Mixxx
                # database at all — e.g. a zero-byte file, or any other valid-but-foreign SQLite
                # file) raises sqlite3.Error and falls through to the "database" exit below.
                # schema_info() deliberately swallows that same error into verdict="missing" (its
                # contract for "can't tell the schema version"), which would otherwise misreport
                # "this isn't a Mixxx database" as "unsupported schema" — those are exit 7 and exit
                # 6 respectively, and the distinction is real: 6 means it IS a Mixxx database, just
                # at a revision we don't support.
                schema_mod.read_schema_version(check)
                info = schema_mod.schema_info(check, ceiling=self._lib.schema_ceiling)
        except sqlite3.Error as exc:
            raise DatabaseError(f"candidate is not a readable Mixxx database: {exc}", path=str(candidate)) from exc
        if result != "ok":
            raise DatabaseError(f"candidate failed integrity_check: {result}", path=str(candidate), integrity=result)
        if info.verdict in ("missing", "unsupported"):
            raise SchemaUnsupportedError(f"restore candidate: {info.message}", path=str(candidate),
                                         version=info.version, ceiling=info.ceiling, verdict=info.verdict)
        db = self._lib.paths.db
        with closing(conn_mod.open_rw(db)) as conn, conn_mod.translating_errors():
            assert_mixxx_closed(conn, process_check=self._lib._process_check)
            pre = backup_mod.create_backup(conn, self._lib.paths.backup_dir, "backup restore")
        staging = db.with_name(db.name + ".mixxxlib-restore")
        try:
            shutil.copyfile(candidate, staging)
            os.replace(staging, db)
        except OSError as exc:
            raise DatabaseError(f"failed to install the restored database: {exc}", path=str(candidate),
                                backup=str(pre)) from exc
        finally:
            staging.unlink(missing_ok=True)
        journal = db.with_name(db.name + "-journal")
        if journal.exists():
            journal.unlink()
        with closing(conn_mod.open_ro(db)) as verify:
            integrity = conn_mod.integrity_check(verify)
        return {"restored_from": str(candidate), "backup": str(pre), "integrity": integrity}
