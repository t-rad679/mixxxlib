from pathlib import Path

import pytest

from mixxxlib.paths import resolve

HOME = Path("/home/u")


def test_linux_defaults():
    r = resolve(environ={}, platform="linux", home=HOME)
    assert r.settings_dir == HOME / ".mixxx"
    assert r.db == HOME / ".mixxx" / "mixxxdb.sqlite"
    assert r.config == HOME / ".mixxx" / "mixxx.cfg"
    assert r.backup_dir == HOME / ".local/share/mixxxlib/backups"


def test_flags_beat_env_beat_defaults():
    env = {"MIXXXLIB_SETTINGS_PATH": "/env/settings", "MIXXXLIB_DB": "/env/db.sqlite",
           "MIXXXLIB_BACKUP_DIR": "/env/backups"}
    r = resolve(settings_path="/flag/settings", db="/flag/db.sqlite", backup_dir="/flag/backups",
                environ=env, platform="linux", home=HOME)
    assert (r.settings_dir, r.db, r.backup_dir) == (Path("/flag/settings"), Path("/flag/db.sqlite"), Path("/flag/backups"))
    r = resolve(environ=env, platform="linux", home=HOME)
    assert (r.settings_dir, r.db, r.backup_dir) == (Path("/env/settings"), Path("/env/db.sqlite"), Path("/env/backups"))


def test_settings_path_alone_derives_db_and_config():
    r = resolve(settings_path="/s", environ={}, platform="linux", home=HOME)
    assert r.db == Path("/s/mixxxdb.sqlite")
    assert r.config == Path("/s/mixxx.cfg")


def test_db_alone_keeps_config_at_default_settings_dir():
    r = resolve(db="/elsewhere/x.sqlite", environ={}, platform="linux", home=HOME)
    assert r.db == Path("/elsewhere/x.sqlite")
    assert r.config == HOME / ".mixxx" / "mixxx.cfg"


def test_xdg_data_home_moves_backup_dir():
    r = resolve(environ={"XDG_DATA_HOME": "/xdg"}, platform="linux", home=HOME)
    assert r.backup_dir == Path("/xdg/mixxxlib/backups")


def test_tilde_is_expanded():
    # environ["HOME"] deliberately disagrees with the injected home, to prove
    # expansion follows the injected home/environ, never the real os.environ.
    r = resolve(db="~/db.sqlite", environ={"HOME": "/not/used"}, platform="linux", home=HOME)
    assert r.db == HOME / "db.sqlite"


def test_macos_prefers_container_dir_when_present(tmp_path):
    container = tmp_path / "Library/Containers/org.mixxx.mixxx/Data/Library/Application Support/Mixxx"
    r = resolve(environ={}, platform="darwin", home=tmp_path)
    assert r.settings_dir == tmp_path / "Library/Application Support/Mixxx"
    container.mkdir(parents=True)
    r = resolve(environ={}, platform="darwin", home=tmp_path)
    assert r.settings_dir == container


def test_windows_uses_localappdata():
    r = resolve(environ={"LOCALAPPDATA": "C:/Users/u/AppData/Local"}, platform="win32", home=Path("C:/Users/u"))
    assert r.settings_dir == Path("C:/Users/u/AppData/Local/Mixxx")
