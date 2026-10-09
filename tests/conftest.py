from __future__ import annotations

import io
import json
import shutil
import sqlite3
from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
AUDIO_REL = "tests/fixtures/audio"
ENV_VARS = ("MIXXXLIB_DB", "MIXXXLIB_SETTINGS_PATH", "MIXXXLIB_BACKUP_DIR",
            "MIXXXLIB_BACKUP_TTL_DAYS", "MIXXXLIB_BACKUP_KEEP_MIN", "MIXXXLIB_DEBUG")


@dataclass
class CliResult:
    code: int
    stdout: str
    stderr: str

    @property
    def json(self) -> Any:
        return json.loads(self.stdout)

    @property
    def error(self) -> Any:
        return json.loads(self.stderr)


def run_cli(*args: str) -> CliResult:
    from mixxxlib.cli import main

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(args))
    return CliResult(code, out.getvalue(), err.getvalue())


@dataclass
class Sandbox:
    settings_dir: Path
    db: Path
    audio: Path
    backup_dir: Path
    extra_args: list[str] = field(default_factory=list)

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"{self.db.resolve().as_uri()}?mode=ro", uri=True)

    def query(self, sql: str, *params: Any) -> list[tuple]:
        with closing(self.connect()) as conn:
            return [tuple(r) for r in conn.execute(sql, params).fetchall()]

    def scalar(self, sql: str, *params: Any) -> Any:
        return self.query(sql, *params)[0][0]

    def run(self, *args: str) -> CliResult:
        return run_cli("--settings-path", str(self.settings_dir), "--backup-dir", str(self.backup_dir),
                       *self.extra_args, *args)

    def library(self, **kwargs: Any):
        from mixxxlib.library import Library

        return Library.open(settings_path=str(self.settings_dir), backup_dir=str(self.backup_dir), **kwargs)


def materialise(fixture_name: str, root: Path) -> Sandbox:
    settings = root / "settings"
    settings.mkdir()
    audio = root / "audio"
    shutil.copytree(FIXTURES / "audio", audio)
    db = settings / "mixxxdb.sqlite"
    shutil.copy(FIXTURES / fixture_name, db)
    shutil.copy(FIXTURES / "mixxx.cfg", settings / "mixxx.cfg")
    with closing(sqlite3.connect(db, autocommit=True)) as conn:
        conn.execute("UPDATE track_locations SET location = ? || substr(location, ?), directory = ?",
                     (f"{audio}/", len(AUDIO_REL) + 2, str(audio)))
        conn.execute("UPDATE directories SET directory = ?", (str(audio),))
        conn.execute("UPDATE LibraryHashes SET directory_path = ?", (str(audio),))
    return Sandbox(settings_dir=settings, db=db, audio=audio, backup_dir=root / "backups")


@pytest.fixture
def clean_env(monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def manifest() -> dict:
    return json.loads((FIXTURES / "manifest.json").read_text())


@pytest.fixture
def manifest_42() -> dict:
    return json.loads((FIXTURES / "manifest-42.json").read_text())


def _doctor_errors(box: Sandbox) -> set[tuple[str, str]]:
    from mixxxlib.library import Library

    lib = Library.open(settings_path=str(box.settings_dir), backup_dir=str(box.backup_dir), process_check=lambda: False)
    return {(f.check, f.message) for f in lib.doctor() if f.severity == "error"}


def _with_doctor_check(box: Sandbox):
    before = _doctor_errors(box)
    yield box
    if box.__dict__.get("_skip_doctor"):
        return
    after = _doctor_errors(box)
    assert after <= before, f"doctor found new error findings: {sorted(after - before)}"


@pytest.fixture
def sandbox(tmp_path, clean_env):
    yield from _with_doctor_check(materialise("mixxxdb.sqlite", tmp_path))


@pytest.fixture
def sandbox_42(tmp_path, clean_env, monkeypatch):
    from mixxxlib.db import schema

    monkeypatch.setattr(schema, "SCHEMA_CEILING", 42)
    yield from _with_doctor_check(materialise("mixxxdb-42.sqlite", tmp_path))
