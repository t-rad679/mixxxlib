import json

from mixxxlib import __version__
from tests.conftest import run_cli


def test_version():
    r = run_cli("--version")
    assert (r.code, r.stdout.strip()) == (0, __version__)


def test_info_json_envelope(sandbox):
    r = sandbox.run("info", "--json")
    assert r.code == 0 and r.stderr == ""
    payload = r.json
    assert payload["ok"] is True and payload["command"] == "info"
    assert payload["warnings"] == [] and "mutation" not in payload
    data = payload["data"]
    assert data["version"] == __version__
    assert data["schema"] == {"version": 39, "ceiling": 40, "capabilities": [], "verdict": "supported",
                              "message": "schema revision 39 is supported"}
    assert data["paths"]["db"] == str(sandbox.db) and data["paths"]["backup_dir"] == str(sandbox.backup_dir)
    assert data["backups"] == {"count": 0, "newest": None}
    assert data["mixxx_running"] in (True, False, None)


def test_global_options_work_before_or_after_the_subcommand(sandbox):
    before = run_cli("--json", "--settings-path", str(sandbox.settings_dir), "info")
    after = run_cli("info", "--settings-path", str(sandbox.settings_dir), "--json")
    assert before.code == after.code == 0
    assert before.json["data"]["paths"] == after.json["data"]["paths"]


def test_info_human_output_is_key_value(sandbox):
    r = sandbox.run("info")
    assert r.code == 0 and "schema:" in r.stdout and "version: 39" in r.stdout


def test_missing_database_is_exit_3_with_json_error_on_stderr(tmp_path, clean_env):
    r = run_cli("--db", str(tmp_path / "none.sqlite"), "info", "--json")
    assert r.code == 3 and r.stdout == ""
    err = r.error
    assert err["ok"] is False and err["error"]["code"] == "not_found" and err["error"]["exit_code"] == 3
    assert err["error"]["details"]["path"].endswith("none.sqlite")
    r = run_cli("--db", str(tmp_path / "none.sqlite"), "info")
    assert r.code == 3 and r.stderr.startswith("mixxxlib: not_found: ")


def test_usage_error_is_exit_2(sandbox):
    r = sandbox.run("info", "--bogus")
    assert r.code == 2 and r.stdout == "" and "--bogus" in r.stderr
    r = sandbox.run("info", "--bogus", "--json")
    assert r.code == 2 and r.error["error"]["code"] == "usage"


def test_settings_show(sandbox):
    r = sandbox.run("settings", "show", "Library.SyncTrackMetadataExport", "--json")
    assert r.code == 0 and r.json["data"] == {"selector": "Library.SyncTrackMetadataExport", "value": "0"}
    r = sandbox.run("settings", "show", "Library")
    assert r.code == 0 and "SyncTrackMetadataExport: 0" in r.stdout
    r = sandbox.run("settings", "show", "--json")
    assert r.code == 0 and "Config" in r.json["data"]["value"]
    r = sandbox.run("settings", "show", "Nope.x", "--json")
    assert r.code == 3 and r.error["error"]["details"] == {"section": "Nope"}


def test_settings_show_without_cfg_is_exit_3(sandbox):
    (sandbox.settings_dir / "mixxx.cfg").unlink()
    r = sandbox.run("settings", "show", "--json")
    assert r.code == 3 and r.error["error"]["code"] == "not_found"
    assert sandbox.run("info", "--json").code == 0  # mixxx.cfg is optional for everything else


# --- carried-forward requirement 1 (from Task 1): no latent traceback escapes -----------------

def test_no_args_prints_help_cleanly_no_traceback():
    r = run_cli()
    assert r.code != -1
    assert "Traceback" not in r.stdout and "Traceback" not in r.stderr
    assert "Usage:" in r.stdout or "Usage:" in r.stderr


def test_malformed_invocation_is_exit_2_no_traceback(sandbox):
    r = sandbox.run("bogus-command")
    assert r.code == 2
    assert "Traceback" not in r.stdout and "Traceback" not in r.stderr


def test_unexpected_internal_exception_is_exit_1_single_line_no_traceback_unless_debug(sandbox, monkeypatch):
    from mixxxlib.cli import info as info_cmd

    def boom(ctx):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(info_cmd, "open_library", boom)

    monkeypatch.delenv("MIXXXLIB_DEBUG", raising=False)
    r = sandbox.run("info")
    assert r.code == 1
    assert r.stdout == ""
    assert r.stderr.strip().splitlines() == ["mixxxlib: internal: RuntimeError: kaboom"]
    assert "Traceback" not in r.stderr

    monkeypatch.setenv("MIXXXLIB_DEBUG", "1")
    r = sandbox.run("info")
    assert r.code == 1
    assert "Traceback" in r.stderr
    assert "mixxxlib: internal: RuntimeError: kaboom" in r.stderr
