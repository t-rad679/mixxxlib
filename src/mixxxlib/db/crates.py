"""All SQL for crates (spec §6.1, §8.2). Works at schema ≤ 40 and, when `nested`, at ≥ 41."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from mixxxlib.db.connection import MutationPlan, Statement
from mixxxlib.errors import NotFoundError, ValidationError
from mixxxlib.model import Crate, CrateNode, WarningMessage

MAX_NAME_LENGTH = 48


def _select(nested: bool) -> str:
    parent = "c.parent_id" if nested else "NULL"
    return (
        f"SELECT c.id, c.name, {parent} AS parent_id, c.locked, c.show, c.autodj_source, "
        "(SELECT count(*) FROM crate_tracks ct WHERE ct.crate_id = c.id) AS track_count FROM crates c"
    )


def row_to_crate(row: sqlite3.Row) -> Crate:
    return Crate(id=row["id"], name=row["name"], parent_id=row["parent_id"], locked=bool(row["locked"]),
                 show=bool(row["show"]), autodj_source=bool(row["autodj_source"]), track_count=row["track_count"])


def list_crates(conn: sqlite3.Connection, *, nested: bool, parent_id: int | None = None,
                roots_only: bool = False) -> list[Crate]:
    sql, params = _select(nested), ()
    if nested and roots_only:
        sql += " WHERE c.parent_id IS NULL"
    elif nested and parent_id is not None:
        sql, params = sql + " WHERE c.parent_id = ?", (parent_id,)
    sql += " ORDER BY c.name, c.id"
    return [row_to_crate(r) for r in conn.execute(sql, params)]


def get_crate_by_id(conn: sqlite3.Connection, crate_id: int, *, nested: bool) -> Crate:
    row = conn.execute(_select(nested) + " WHERE c.id = ?", (crate_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"crate {crate_id} does not exist", crate_id=crate_id)
    return row_to_crate(row)


def find_crate_by_name(conn: sqlite3.Connection, name: str, *, nested: bool, parent_id: int | None = None,
                       parent_given: bool = False) -> Crate:
    sql, params = _select(nested) + " WHERE c.name = ?", [name]
    if nested and parent_given:
        sql += " AND c.parent_id IS ?"
        params.append(parent_id)
    rows = conn.execute(sql + " ORDER BY c.id", params).fetchall()
    if not rows:
        raise NotFoundError(f"crate {name!r} does not exist", name=name)
    if len(rows) > 1:
        raise ValidationError(
            f"crate name {name!r} matches {len(rows)} crates under different parents; use --parent or --id",
            name=name, candidates=[{"id": r["id"], "parent_id": r["parent_id"]} for r in rows],
        )
    return row_to_crate(rows[0])


def descendant_ids(conn: sqlite3.Connection, crate_id: int) -> list[int]:
    """Every crate below crate_id, parents before children. Requires the parent_id column."""
    rows = conn.execute(
        "WITH RECURSIVE sub(id, depth) AS ("
        "  SELECT id, 1 FROM crates WHERE parent_id = ?"
        "  UNION ALL SELECT c.id, sub.depth + 1 FROM crates c JOIN sub ON c.parent_id = sub.id WHERE sub.depth < 64)"
        " SELECT id FROM sub ORDER BY depth, id", (crate_id,)
    ).fetchall()
    return [r[0] for r in rows]


def tree(conn: sqlite3.Connection, *, nested: bool) -> list[CrateNode]:
    crates = list_crates(conn, nested=nested)
    by_parent: dict[int | None, list[Crate]] = {}
    for crate in crates:
        by_parent.setdefault(crate.parent_id, []).append(crate)
    ids = {c.id for c in crates}

    def build(crate: Crate, seen: frozenset[int]) -> CrateNode:
        children = tuple(build(child, seen | {child.id}) for child in by_parent.get(crate.id, ()) if child.id not in seen)
        return CrateNode(crate, children)

    roots = [c for c in crates if c.parent_id is None or c.parent_id not in ids]
    return [build(root, frozenset({root.id})) for root in roots]


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("crate name must not be empty", name=name)
    if len(name) > MAX_NAME_LENGTH:
        raise ValidationError(f"crate name is {len(name)} characters; Mixxx allows at most {MAX_NAME_LENGTH}",
                              name=name, length=len(name), max_length=MAX_NAME_LENGTH)
    return name


def sibling_exists(conn: sqlite3.Connection, name: str, parent_id: int | None, *, nested: bool,
                   exclude_id: int | None = None) -> bool:
    sql, params = "SELECT 1 FROM crates WHERE name = ?", [name]
    if nested:
        sql += " AND parent_id IS ?"
        params.append(parent_id)
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return conn.execute(sql + " LIMIT 1", params).fetchone() is not None


def _refuse_duplicate(conn: sqlite3.Connection, name: str, parent_id: int | None, *, nested: bool,
                      exclude_id: int | None = None) -> None:
    if sibling_exists(conn, name, parent_id, nested=nested, exclude_id=exclude_id):
        where = f"under crate {parent_id}" if parent_id is not None else "at the root"
        raise ValidationError(f"a crate named {name!r} already exists {where}", name=name, parent_id=parent_id)


def member_ids(conn: sqlite3.Connection, crate_id: int) -> set[int]:
    return {r[0] for r in conn.execute("SELECT track_id FROM crate_tracks WHERE crate_id = ?", (crate_id,))}


def existing_track_ids(conn: sqlite3.Connection, ids: Iterable[int]) -> set[int]:
    wanted = list(dict.fromkeys(ids))
    found: set[int] = set()
    for start in range(0, len(wanted), 500):
        chunk = wanted[start:start + 500]
        marks = ",".join("?" * len(chunk))
        found |= {r[0] for r in conn.execute(f"SELECT id FROM library WHERE id IN ({marks})", chunk)}
    return found


def ensure_unlocked(crate: Crate) -> None:
    if crate.locked:
        raise ValidationError(f"crate {crate.name!r} is locked; run `crate update --no-locked` first",
                              crate_id=crate.id, name=crate.name)


def _refresh(crate_id: int, nested: bool):
    return lambda conn: {"crate": get_crate_by_id(conn, crate_id, nested=nested)}


def plan_create(conn: sqlite3.Connection, name: str, *, parent: Crate | None, nested: bool) -> MutationPlan:
    validate_name(name)
    parent_id = parent.id if parent else None
    _refuse_duplicate(conn, name, parent_id, nested=nested)
    if nested:
        statement = Statement("INSERT INTO crates (name, parent_id) VALUES (?, ?)", (name, parent_id))
    else:
        statement = Statement("INSERT INTO crates (name) VALUES (?)", (name,))
    preview = Crate(id=-1, name=name, parent_id=parent_id, locked=False, show=True, autodj_source=False, track_count=0)

    def after_commit(conn: sqlite3.Connection) -> dict:
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"crate": get_crate_by_id(conn, new_id, nested=nested)}

    return MutationPlan(statements=[statement], data={"crate": preview}, after_commit=after_commit)


def plan_rename(conn: sqlite3.Connection, crate: Crate, new_name: str, *, nested: bool) -> MutationPlan:
    validate_name(new_name)
    _refuse_duplicate(conn, new_name, crate.parent_id, nested=nested, exclude_id=crate.id)
    return MutationPlan(statements=[Statement("UPDATE crates SET name = ? WHERE id = ?", (new_name, crate.id))],
                        data={"crate": crate}, after_commit=_refresh(crate.id, nested))


def plan_move(conn: sqlite3.Connection, crate: Crate, *, parent: Crate | None) -> MutationPlan:
    new_parent_id = parent.id if parent else None
    if parent is not None and (parent.id == crate.id or parent.id in descendant_ids(conn, crate.id)):
        raise ValidationError(f"moving crate {crate.name!r} under {parent.name!r} would create a cycle",
                              crate_id=crate.id, parent_id=parent.id, reason="cycle")
    _refuse_duplicate(conn, crate.name, new_parent_id, nested=True, exclude_id=crate.id)
    return MutationPlan(statements=[Statement("UPDATE crates SET parent_id = ? WHERE id = ?", (new_parent_id, crate.id))],
                        data={"crate": crate}, after_commit=_refresh(crate.id, True))


def plan_update(conn: sqlite3.Connection, crate: Crate, *, locked: bool | None, show: bool | None,
                autodj_source: bool | None, nested: bool) -> MutationPlan:
    changes = {k: v for k, v in (("locked", locked), ("show", show), ("autodj_source", autodj_source)) if v is not None}
    if not changes:
        raise ValidationError("nothing to update: pass at least one of --locked/--show/--autodj-source", crate_id=crate.id)
    assignments = ", ".join(f"{column} = ?" for column in changes)
    statement = Statement(f"UPDATE crates SET {assignments} WHERE id = ?", (*(int(v) for v in changes.values()), crate.id))
    return MutationPlan(statements=[statement], data={"crate": crate}, after_commit=_refresh(crate.id, nested))


def plan_delete(conn: sqlite3.Connection, crate: Crate, *, recursive: bool, nested: bool) -> MutationPlan:
    ensure_unlocked(crate)
    children = descendant_ids(conn, crate.id) if nested else []
    if children and not recursive:
        raise ValidationError(f"crate {crate.name!r} has {len(children)} descendant crate(s); pass --recursive to delete the subtree",
                              crate_id=crate.id, children=sorted(children))
    victims = [get_crate_by_id(conn, cid, nested=nested) for cid in reversed(children)] + [crate]
    for victim in victims:
        ensure_unlocked(victim)
    statements: list[Statement] = []
    for victim in victims:
        statements.append(Statement("DELETE FROM crate_tracks WHERE crate_id = ?", (victim.id,)))
        statements.append(Statement("DELETE FROM crates WHERE id = ?", (victim.id,)))
    return MutationPlan(statements=statements,
                        data={"deleted": [{"crate": v, "track_count": v.track_count} for v in victims]})


def _check_tracks_exist(conn: sqlite3.Connection, track_ids: list[int]) -> None:
    unknown = sorted(set(track_ids) - existing_track_ids(conn, track_ids))
    if unknown:
        raise ValidationError(f"{len(unknown)} track id(s) do not exist: {unknown[:10]}", track_ids=unknown)


def plan_add(conn: sqlite3.Connection, crate: Crate, track_ids: Iterable[int], *, nested: bool) -> MutationPlan:
    ensure_unlocked(crate)
    wanted = list(dict.fromkeys(int(t) for t in track_ids))
    _check_tracks_exist(conn, wanted)
    current = member_ids(conn, crate.id)
    added = [t for t in wanted if t not in current]
    already = [t for t in wanted if t in current]
    warnings = [WarningMessage("already_member", f"{len(already)} track(s) were already in the crate", {"track_ids": already})] if already else []
    statements = [Statement("INSERT INTO crate_tracks (crate_id, track_id) VALUES (?, ?)", (crate.id, t)) for t in added]
    return MutationPlan(statements=statements, data={"crate": crate, "added": added, "already_member": already},
                        warnings=warnings, after_commit=lambda c: {"crate": get_crate_by_id(c, crate.id, nested=nested),
                                                                   "added": added, "already_member": already})


def plan_remove(conn: sqlite3.Connection, crate: Crate, track_ids: Iterable[int], *, nested: bool) -> MutationPlan:
    ensure_unlocked(crate)
    wanted = list(dict.fromkeys(int(t) for t in track_ids))
    current = member_ids(conn, crate.id)
    removed = [t for t in wanted if t in current]
    missing = [t for t in wanted if t not in current]
    warnings = [WarningMessage("not_member", f"{len(missing)} track(s) were not in the crate", {"track_ids": missing})] if missing else []
    statements = [Statement("DELETE FROM crate_tracks WHERE crate_id = ? AND track_id = ?", (crate.id, t)) for t in removed]
    return MutationPlan(statements=statements, data={"crate": crate, "removed": removed, "not_member": missing},
                        warnings=warnings, after_commit=lambda c: {"crate": get_crate_by_id(c, crate.id, nested=nested),
                                                                   "removed": removed, "not_member": missing})


def plan_set_tracks(conn: sqlite3.Connection, crate: Crate, track_ids: Iterable[int], *, nested: bool) -> MutationPlan:
    ensure_unlocked(crate)
    wanted = list(dict.fromkeys(int(t) for t in track_ids))
    _check_tracks_exist(conn, wanted)
    current = member_ids(conn, crate.id)
    added = [t for t in wanted if t not in current]
    removed = sorted(current - set(wanted))
    unchanged = [t for t in wanted if t in current]
    statements = [Statement("DELETE FROM crate_tracks WHERE crate_id = ? AND track_id = ?", (crate.id, t)) for t in removed]
    statements += [Statement("INSERT INTO crate_tracks (crate_id, track_id) VALUES (?, ?)", (crate.id, t)) for t in added]
    payload = {"added": added, "removed": removed, "unchanged": unchanged}
    return MutationPlan(statements=statements, data={"crate": crate, **payload},
                        after_commit=lambda c: {"crate": get_crate_by_id(c, crate.id, nested=nested), **payload})


def export_m3u8(conn: sqlite3.Connection, crate_ids: list[int]) -> str:
    """#EXTM3U text with #EXTINF:<seconds>,<artist> - <title> and absolute paths, tracks by id."""
    marks = ",".join("?" * len(crate_ids))
    rows = conn.execute(
        "SELECT DISTINCT l.id, l.artist, l.title, l.duration, tl.location FROM library l "
        "JOIN track_locations tl ON tl.id = l.location JOIN crate_tracks ct ON ct.track_id = l.id "
        f"WHERE ct.crate_id IN ({marks}) ORDER BY l.id", crate_ids,
    ).fetchall()
    lines = ["#EXTM3U"]
    for row in rows:
        seconds = int(round(row["duration"] or 0))
        lines.append(f"#EXTINF:{seconds},{row['artist'] or ''} - {row['title'] or ''}")
        lines.append(row["location"] or "")
    return "\n".join(lines) + "\n"
