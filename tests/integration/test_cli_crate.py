import io
import json

import pytest


def test_list_show_tree(sandbox, manifest):
    r = sandbox.run("crate", "list", "--json")
    names = [c["name"] for c in r.json["data"]]
    assert names == sorted(names) and "Fixture Loops" in names
    assert set(r.json["data"][0]) == {"id", "name", "parent_id", "locked", "show", "autodj_source", "track_count"}
    r = sandbox.run("crate", "show", "Fixture Loops", "--json")
    assert r.json["data"] == {"crate": r.json["data"]["crate"]} and r.json["data"]["crate"]["track_count"] == 5
    r = sandbox.run("crate", "show", "Fixture Loops", "--tracks", "--json")
    assert [t["id"] for t in r.json["data"]["tracks"]] == sorted(manifest["crate_members"]["loops"])
    r = sandbox.run("crate", "show", str(manifest["crates"]["loops"]), "--id")
    assert r.code == 0 and "name: Fixture Loops" in r.stdout
    r = sandbox.run("crate", "tree", "--json")
    assert all(node["children"] == [] for node in r.json["data"])
    r = sandbox.run("crate", "list")
    assert r.stdout.splitlines()[0].startswith("id  name")


def test_create_rename_update_delete(sandbox):
    r = sandbox.run("crate", "create", "Fresh", "--json")
    assert r.code == 0 and r.json["data"]["crate"]["name"] == "Fresh"
    assert r.json["mutation"]["dry_run"] is False and r.json["mutation"]["integrity"] == "ok" and r.json["mutation"]["statements"] == 1
    assert r.json["mutation"]["backup"].startswith(str(sandbox.backup_dir))
    r = sandbox.run("crate", "create", "Fresh", "--json")
    assert r.code == 4 and r.error["error"]["code"] == "validation"
    r = sandbox.run("crate", "rename", "Fresh", "Fresher", "--json")
    assert r.json["data"]["crate"]["name"] == "Fresher"
    r = sandbox.run("crate", "update", "Fresher", "--locked", "--no-show", "--json")
    c = r.json["data"]["crate"]
    assert (c["locked"], c["show"]) == (True, False)
    r = sandbox.run("crate", "delete", "Fresher", "--yes", "--json")
    assert r.code == 4  # locked
    sandbox.run("crate", "update", "Fresher", "--no-locked")
    r = sandbox.run("crate", "delete", "Fresher", "--json")
    assert r.code == 8 and r.error["error"]["code"] == "confirmation_required"
    r = sandbox.run("crate", "delete", "Fresher", "--yes", "--json")
    assert r.code == 0 and r.json["data"]["deleted"][0]["crate"]["name"] == "Fresher"
    assert sandbox.scalar("SELECT count(*) FROM crates WHERE name = 'Fresher'") == 0


def test_membership_commands(sandbox, manifest, monkeypatch):
    t = manifest["tracks"]
    r = sandbox.run("crate", "add", "Empty Crate", str(t[0]), str(t[1]), "--json")
    assert r.code == 0 and r.json["data"]["added"] == [t[0], t[1]]
    r = sandbox.run("crate", "add", "Empty Crate", str(t[1]), "--json")
    assert r.json["data"]["already_member"] == [t[1]] and r.json["warnings"][0]["code"] == "already_member"
    r = sandbox.run("crate", "add", "Empty Crate", "999999", "--json")
    assert r.code == 4 and r.error["error"]["details"] == {"track_ids": [999999]}
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{t[2]}\n\n{t[3]}\n"))
    r = sandbox.run("crate", "add", "Empty Crate", "--stdin", "--json")
    assert r.json["data"]["added"] == [t[2], t[3]]
    path = sandbox.audio / manifest["audio"][str(t[4])]
    r = sandbox.run("crate", "add", "Empty Crate", str(path), "--paths", "--json")
    assert r.json["data"]["added"] == [t[4]]
    r = sandbox.run("crate", "add", "Empty Crate", "/nowhere.aiff", "--paths", "--json")
    assert r.code == 4
    r = sandbox.run("crate", "remove", "Empty Crate", str(t[0]), "--json")
    assert r.code == 8
    r = sandbox.run("crate", "remove", "Empty Crate", str(t[0]), str(t[9]), "--yes", "--json")
    assert r.json["data"] == {"crate": r.json["data"]["crate"], "removed": [t[0]], "not_member": [t[9]]}
    r = sandbox.run("crate", "set-tracks", "Empty Crate", str(t[1]), str(t[7]), "--json")
    assert r.code == 8 and r.error["error"]["code"] == "confirmation_required"
    r = sandbox.run("crate", "set-tracks", "Empty Crate", str(t[1]), str(t[7]), "--yes", "--json")
    d = r.json["data"]
    assert (d["added"], sorted(d["removed"]), d["unchanged"]) == ([t[7]], sorted([t[2], t[3], t[4]]), [t[1]])
    r = sandbox.run("crate", "set-tracks", "Empty Crate", "--yes", "--json")
    assert r.json["data"]["crate"]["track_count"] == 0


def test_dry_run_makes_no_change_and_no_backup(sandbox, manifest):
    r = sandbox.run("crate", "delete", "Fixture Loops", "--dry-run", "--json")
    assert r.code == 0 and r.json["mutation"]["dry_run"] is True and r.json["mutation"]["backup"] is None
    assert r.json["mutation"]["statements"][0].startswith("DELETE FROM crate_tracks WHERE crate_id = ")
    assert sandbox.scalar("SELECT count(*) FROM crates WHERE name = 'Fixture Loops'") == 1
    assert not sandbox.backup_dir.exists()
    r = sandbox.run("crate", "create", "Dry", "--dry-run")
    assert r.code == 0 and "dry run" in r.stdout and "INSERT INTO crates" in r.stdout


def test_dry_run_create_sentinel_id_is_flagged_by_the_envelope(sandbox):
    """Carried-forward item 1 (Task 10/12): a dry-run create's placeholder Crate(id=-1) passes
    through to JSON as-is; a script must be able to tell it apart from a real id by looking at
    mutation.dry_run rather than trusting the id."""
    r = sandbox.run("crate", "create", "Sentinel", "--dry-run", "--json")
    assert r.code == 0
    assert r.json["data"]["crate"]["id"] == -1
    assert r.json["mutation"]["dry_run"] is True
    assert sandbox.scalar("SELECT count(*) FROM crates WHERE name = 'Sentinel'") == 0


def test_export_m3u8_ignores_json(sandbox, manifest):
    r = sandbox.run("crate", "export", "Fixture Loops", "--json")
    assert r.code == 0 and r.stdout.startswith("#EXTM3U\n")
    with pytest.raises(json.JSONDecodeError):
        r.json
    r = sandbox.run("crate", "export", "Fixture Loops", "--format", "xspf", "--json")
    assert r.code == 4


def test_nested_commands_below_41(sandbox):
    assert sandbox.run("crate", "move", "Empty Crate", "--root", "--json").code == 6
    assert sandbox.run("crate", "create", "Kid", "--parent", "Empty Crate", "--json").code == 6
    assert sandbox.run("crate", "list", "--parent", "Empty Crate", "--json").code == 6
    assert sandbox.run("crate", "show", "Empty Crate", "--recursive", "--json").code == 6
    r = sandbox.run("crate", "list", "--root", "--json")
    assert r.code == 0 and len(r.json["data"]) == sandbox.scalar("SELECT count(*) FROM crates")


def test_nested_commands_at_42(sandbox_42, manifest_42):
    c = manifest_42["crates"]
    r = sandbox_42.run("crate", "show", "Dupe", "--json")
    assert r.code == 4 and sorted(x["id"] for x in r.error["error"]["details"]["candidates"]) == sorted([c["dupe_a"], c["dupe_b"]])
    r = sandbox_42.run("crate", "show", "Bass", "--parent", "Genres", "--tracks", "--recursive", "--json")
    assert [t["id"] for t in r.json["data"]["tracks"]] == sorted(manifest_42["crate_members"]["bass"] + manifest_42["crate_members"]["dubstep"])
    r = sandbox_42.run("crate", "tree", "--json")
    genres = next(n for n in r.json["data"] if n["crate"]["name"] == "Genres")
    assert genres["children"][0]["crate"]["name"] == "Bass" and genres["children"][0]["children"][0]["crate"]["name"] == "Dubstep"
    r = sandbox_42.run("crate", "list", "--parent", "Genres", "--json")
    assert [x["name"] for x in r.json["data"]] == ["Bass"]
    r = sandbox_42.run("crate", "create", "Dupe", "--json")
    assert r.code == 4
    r = sandbox_42.run("crate", "create", "Techno", "--parent", "Genres", "--json")
    assert r.json["data"]["crate"]["parent_id"] == c["genres"]
    techno_id = r.json["data"]["crate"]["id"]
    r = sandbox_42.run("crate", "move", "Dubstep", "--parent", "Techno", "--json")
    assert r.code == 0 and r.json["data"]["crate"]["parent_id"] == techno_id
    r = sandbox_42.run("crate", "move", "Genres", "--parent", "Dubstep", "--json")
    assert r.code == 4 and r.error["error"]["details"]["reason"] == "cycle"
    r = sandbox_42.run("crate", "move", "Dubstep", "--root", "--json")
    assert r.json["data"]["crate"]["parent_id"] is None
    r = sandbox_42.run("crate", "delete", "Genres", "--yes", "--json")
    assert r.code == 4 and "children" in r.error["error"]["details"]
    r = sandbox_42.run("crate", "delete", "Genres", "--yes", "--recursive", "--json")
    assert sorted(d["crate"]["name"] for d in r.json["data"]["deleted"]) == ["Bass", "Genres", "Techno"]
    r = sandbox_42.run("crate", "tree")
    assert r.code == 0 and "Dubstep" in r.stdout


def test_yes_checked_after_selector_resolves_and_dry_run_skips_it(sandbox):
    """Decision 7 / global constraints: --yes is checked after the crate selector resolves and
    before the mutation runs, and --dry-run never requires --yes."""
    r = sandbox.run("crate", "delete", "No Such Crate", "--json")
    assert r.code == 3 and r.error["error"]["code"] == "not_found"
    r = sandbox.run("crate", "delete", "Empty Crate", "--json")
    assert r.code == 8 and r.error["error"]["code"] == "confirmation_required"
    r = sandbox.run("crate", "delete", "Empty Crate", "--dry-run", "--json")
    assert r.code == 0 and r.json["mutation"]["dry_run"] is True
    assert sandbox.scalar("SELECT count(*) FROM crates WHERE name = 'Empty Crate'") == 1
