import pytest

from mixxxlib.errors import NotFoundError, SchemaUnsupportedError, ValidationError
from mixxxlib.model import Crate, CrateNode


def test_list_is_sorted_by_name_with_live_track_count(sandbox, manifest):
    crates = sandbox.library().crates.list()
    assert [c.name for c in crates] == sorted(c.name for c in crates)
    loops = next(c for c in crates if c.id == manifest["crates"]["loops"])
    assert loops == Crate(loops.id, "Fixture Loops", None, False, True, False, 5)


def test_get_by_name_and_id(sandbox, manifest):
    lib = sandbox.library()
    assert lib.crates.get("Fixture Loops").id == manifest["crates"]["loops"]
    assert lib.crates.get(manifest["crates"]["loops"], by_id=True).name == "Fixture Loops"
    with pytest.raises(NotFoundError) as e:
        lib.crates.get("Nope")
    assert e.value.details == {"name": "Nope"}
    with pytest.raises(NotFoundError):
        lib.crates.get(999999, by_id=True)


def test_create_rename_update(sandbox):
    lib = sandbox.library()
    result = lib.crates.create("New Crate")
    crate = result.data["crate"]
    assert crate.name == "New Crate" and crate.track_count == 0 and crate.parent_id is None
    assert result.backup and result.integrity == "ok"
    with pytest.raises(ValidationError) as e:
        lib.crates.create("New Crate")
    assert e.value.details["name"] == "New Crate"
    for bad in ["", " ", "x" * 49]:
        with pytest.raises(ValidationError):
            lib.crates.create(bad)
    renamed = lib.crates.rename(crate, "Renamed").data["crate"]
    assert renamed.name == "Renamed" and renamed.id == crate.id
    updated = lib.crates.update(renamed, locked=True, autodj_source=True).data["crate"]
    assert (updated.locked, updated.show, updated.autodj_source) == (True, True, True)
    with pytest.raises(ValidationError):
        lib.crates.update(updated)


def test_locked_crate_refuses_membership_and_deletion(sandbox, manifest):
    lib = sandbox.library()
    locked = lib.crates.get("Locked Crate")
    for call in (lambda: lib.crates.add(locked, [manifest["tracks"][0]]),
                 lambda: lib.crates.remove(locked, manifest["crate_members"]["locked"]),
                 lambda: lib.crates.set_tracks(locked, []),
                 lambda: lib.crates.delete(locked)):
        with pytest.raises(ValidationError) as e:
            call()
        assert e.value.details["crate_id"] == locked.id
    assert not sandbox.backup_dir.exists()
    unlocked = lib.crates.update(locked, locked=False).data["crate"]
    assert lib.crates.delete(unlocked).data["deleted"][0]["track_count"] == 2
    assert sandbox.scalar("SELECT count(*) FROM crate_tracks WHERE crate_id = ?", locked.id) == 0


def test_add_is_idempotent_and_refuses_unknown_tracks_before_writing(sandbox, manifest):
    lib = sandbox.library()
    crate = lib.crates.get("Empty Crate")
    t = manifest["tracks"]
    with pytest.raises(ValidationError) as e:
        lib.crates.add(crate, [t[0], 987654])
    assert e.value.details == {"track_ids": [987654]}
    assert sandbox.scalar("SELECT count(*) FROM crate_tracks WHERE crate_id = ?", crate.id) == 0
    result = lib.crates.add(crate, [t[0], t[1], t[0]])
    assert result.data["added"] == [t[0], t[1]] and result.data["already_member"] == []
    result = lib.crates.add(crate, [t[1], t[2]])
    assert result.data["added"] == [t[2]] and result.data["already_member"] == [t[1]]
    assert [w.code for w in result.warnings] == ["already_member"]
    result = lib.crates.remove(crate, [t[2], t[9]])
    assert result.data["removed"] == [t[2]] and result.data["not_member"] == [t[9]]
    assert [w.code for w in result.warnings] == ["not_member"]
    result = lib.crates.set_tracks(crate, [t[1], t[5]])
    assert (result.data["added"], result.data["removed"], result.data["unchanged"]) == ([t[5]], [t[0]], [t[1]])
    assert sorted(lib.crates.members(crate)) == sorted([t[1], t[5]])


def test_dry_run_renders_statements_only(sandbox, manifest):
    lib = sandbox.library()
    crate = lib.crates.get("Empty Crate")
    result = lib.crates.add(crate, [manifest["tracks"][0]], dry_run=True)
    assert result.dry_run and result.backup is None
    assert result.statements == [f"INSERT INTO crate_tracks (crate_id, track_id) VALUES ({crate.id}, {manifest['tracks'][0]})"]
    assert sandbox.scalar("SELECT count(*) FROM crate_tracks WHERE crate_id = ?", crate.id) == 0


def test_export_m3u8(sandbox, manifest):
    lib = sandbox.library()
    text = lib.crates.export_m3u8(lib.crates.get("Fixture Loops"))
    lines = text.splitlines()
    assert lines[0] == "#EXTM3U"
    assert len(lines) == 1 + 2 * 5
    assert lines[1].startswith("#EXTINF:") and " - " in lines[1]
    assert lines[2] == str(sandbox.audio / manifest["audio"][str(manifest["crate_members"]["loops"][0])])


def test_nested_commands_need_schema_41(sandbox):
    lib = sandbox.library()
    crate = lib.crates.get("Empty Crate")
    assert lib.nested is False
    for call in (lambda: lib.crates.create("Child", parent=crate),
                 lambda: lib.crates.move(crate, parent=None),
                 lambda: lib.crates.list(parent=crate),
                 lambda: lib.crates.members(crate, recursive=True)):
        with pytest.raises(SchemaUnsupportedError) as e:
            call()
        assert e.value.details["needs"] == 41
    roots = lib.crates.tree()
    assert all(node.children == () for node in roots) and len(roots) == len(lib.crates.list())


def test_roots_only_on_a_flat_schema_matches_the_unfiltered_list_in_either_direction(sandbox):
    """Final-review item 7: nothing covered CratesAPI.list(roots_only=True) against a flat
    schema, in either direction. On a flat schema parent_id is always NULL by construction (the
    crates table doesn't even have the column, per _select(nested=False)), so "every crate is a
    root" is trivially true and asserting only `all(... parent_id is None ...)` would pass even
    if roots_only silently filtered something out (or in) that it shouldn't -- the genuinely
    discriminating check is that roots_only=True returns exactly the same set as roots_only=False,
    both matching the table's own row count."""
    lib = sandbox.library()
    assert lib.nested is False
    total = sandbox.scalar("SELECT count(*) FROM crates")
    roots = lib.crates.list(roots_only=True)
    everything = lib.crates.list(roots_only=False)
    assert len(roots) == len(everything) == total > 0
    assert {c.id for c in roots} == {c.id for c in everything}
    assert all(c.parent_id is None for c in roots)


def test_nested_tree_lookup_and_sibling_uniqueness(sandbox_42, manifest_42):
    lib = sandbox_42.library()
    assert lib.nested and lib.schema.version == 42
    c = manifest_42["crates"]
    with pytest.raises(ValidationError) as e:
        lib.crates.get("Dupe")
    assert sorted(x["id"] for x in e.value.details["candidates"]) == sorted([c["dupe_a"], c["dupe_b"]])
    genres = lib.crates.get("Genres")
    bass = lib.crates.get("Bass", parent=genres)
    assert bass.parent_id == genres.id
    tree = lib.crates.tree()
    genres_node = next(n for n in tree if n.crate.id == genres.id)
    assert isinstance(genres_node, CrateNode)
    assert [n.crate.name for n in genres_node.children] == ["Bass"]
    assert [n.crate.name for n in genres_node.children[0].children] == ["Dubstep"]
    assert [x.name for x in lib.crates.list(parent=genres)] == ["Bass"]
    assert all(x.parent_id is None for x in lib.crates.list(roots_only=True))
    with pytest.raises(ValidationError):
        lib.crates.create("Dupe")  # root duplicate: SQLite would allow it, we refuse
    with pytest.raises(ValidationError):
        lib.crates.create("Bass", parent=genres)
    child = lib.crates.create("Bass", parent=bass).data["crate"]  # same name under a different parent is fine
    assert child.parent_id == bass.id
    assert sorted(lib.crates.members(genres, recursive=True)) == sorted(manifest_42["crate_members"]["bass"] + manifest_42["crate_members"]["dubstep"])
    assert lib.crates.members(genres) == []


def test_nested_move_and_delete(sandbox_42, manifest_42):
    lib = sandbox_42.library()
    genres, bass, dubstep = (lib.crates.get(n) for n in ("Genres", "Bass", "Dubstep"))
    with pytest.raises(ValidationError) as e:
        lib.crates.move(genres, parent=dubstep)
    assert e.value.details["reason"] == "cycle"
    with pytest.raises(ValidationError):
        lib.crates.move(bass, parent=bass)
    moved = lib.crates.move(dubstep, parent=None).data["crate"]
    assert moved.parent_id is None
    moved = lib.crates.move(moved, parent=genres).data["crate"]
    assert moved.parent_id == genres.id
    with pytest.raises(ValidationError) as e:
        lib.crates.delete(genres)
    assert e.value.details["children"] == sorted([bass.id, dubstep.id])
    result = lib.crates.delete(genres, recursive=True)
    assert sorted(d["crate"].id for d in result.data["deleted"]) == sorted([genres.id, bass.id, dubstep.id])
    assert sandbox_42.scalar("SELECT count(*) FROM crates WHERE id IN (?, ?, ?)", genres.id, bass.id, dubstep.id) == 0
    assert sandbox_42.scalar("SELECT count(*) FROM crate_tracks WHERE crate_id IN (?, ?)", bass.id, dubstep.id) == 0


def test_library_open_reports_missing_database(tmp_path, clean_env):
    from mixxxlib import Library

    with pytest.raises(NotFoundError) as e:
        Library.open(db=str(tmp_path / "none.sqlite"))
    assert e.value.details["path"].endswith("none.sqlite")
