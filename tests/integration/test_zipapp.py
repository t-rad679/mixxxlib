import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def zipapp(tmp_path_factory):
    if shutil.which("shiv") is None and not (ROOT / ".venv/bin/shiv").exists():
        pytest.skip("shiv not installed")
    out = tmp_path_factory.mktemp("dist") / "mixxxlib"
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    subprocess.run([str(ROOT / "scripts/build-zipapp.sh"), str(out)], check=True, cwd=ROOT, env=env,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert out.exists() and os.access(out, os.X_OK)
    return out


def run(zipapp, box, *args):
    env = {**os.environ, "SHIV_ROOT": str(box.settings_dir.parent / "shiv-cache")}
    return subprocess.run([str(zipapp), "--settings-path", str(box.settings_dir), "--backup-dir", str(box.backup_dir), *args],
                          capture_output=True, text=True, env=env)


def test_zipapp_info_and_doctor_match_the_venv_run(zipapp, sandbox):
    for args in (["info", "--json"], ["doctor", "--json"]):
        bundled = run(zipapp, sandbox, *args)
        native = sandbox.run(*args)
        assert bundled.returncode == native.code, bundled.stderr
        assert json.loads(bundled.stdout)["ok"] is True
        assert json.loads(bundled.stdout)["data"]["summary" if args[0] == "doctor" else "schema"] == native.json["data"]["summary" if args[0] == "doctor" else "schema"]
    missing = run(zipapp, sandbox, "track", "show", "999999", "--json")
    assert missing.returncode == 3 and json.loads(missing.stderr)["error"]["code"] == "not_found"
