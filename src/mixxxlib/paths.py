"""Database, config and backup-directory resolution (spec §9, §7.6)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DB_FILENAME = "mixxxdb.sqlite"
CONFIG_FILENAME = "mixxx.cfg"


@dataclass(frozen=True)
class ResolvedPaths:
    settings_dir: Path
    db: Path
    config: Path
    backup_dir: Path


def default_settings_dir(*, platform: str, home: Path, environ: Mapping[str, str]) -> Path:
    if platform == "darwin":
        container = home / "Library/Containers/org.mixxx.mixxx/Data/Library/Application Support/Mixxx"
        if container.is_dir():
            return container
        return home / "Library/Application Support/Mixxx"
    if platform.startswith("win"):
        local = environ.get("LOCALAPPDATA")
        base = Path(local) if local else home / "AppData/Local"
        return base / "Mixxx"
    return home / ".mixxx"


def default_backup_dir(*, home: Path, environ: Mapping[str, str]) -> Path:
    xdg = environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else home / ".local/share"
    return base / "mixxxlib" / "backups"


def _expand(value: str) -> Path:
    return Path(os.path.expanduser(value))


def resolve(
    settings_path: str | os.PathLike[str] | None = None,
    db: str | os.PathLike[str] | None = None,
    backup_dir: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ResolvedPaths:
    """Precedence: explicit argument, then MIXXXLIB_* environment, then platform default."""
    env = os.environ if environ is None else environ
    plat = sys.platform if platform is None else platform
    home_dir = Path.home() if home is None else Path(home)

    settings_value = settings_path or env.get("MIXXXLIB_SETTINGS_PATH")
    settings_dir = _expand(str(settings_value)) if settings_value else default_settings_dir(
        platform=plat, home=home_dir, environ=env
    )
    db_value = db or env.get("MIXXXLIB_DB")
    db_path = _expand(str(db_value)) if db_value else settings_dir / DB_FILENAME
    backup_value = backup_dir or env.get("MIXXXLIB_BACKUP_DIR")
    backup_path = _expand(str(backup_value)) if backup_value else default_backup_dir(home=home_dir, environ=env)
    return ResolvedPaths(settings_dir=settings_dir, db=db_path, config=settings_dir / CONFIG_FILENAME, backup_dir=backup_path)
