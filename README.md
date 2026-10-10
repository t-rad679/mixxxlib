# mixxxlib

A deterministic, offline command-line tool and Python library for reading and writing a
[Mixxx](https://mixxx.org) library: the `mixxxdb.sqlite` database and the audio files' tags.
Built for programs first (`--json` everywhere, stable exit codes) and people second.

## What it does and does not do

**Does:** crate CRUD and membership (including the `locked` / `show` / `autodj_source` flags);
every user-facing track metadata field, written to the audio file and the database together;
cue points (read and write); beatgrid and key (read only); a `doctor` health check; `mixxx.cfg`
introspection; automatic, pruned database backups before every change.

**Does not:** run while Mixxx is running; write the `beats` or `keys` blobs; control a live
Mixxx; hold any opinion about what your metadata means. Playlists are not in v1. Nested
crates are prepared for (see below) but Mixxx does not ship them yet.

## Install

### Zipapp (recommended)

The release artifact is a single executable file that bundles the package and its two
dependencies. The only thing the machine needs is Python 3.12 or newer.

```sh
# download dist/mixxxlib from a release, or build it from a checkout:
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && scripts/build-zipapp.sh
chmod +x dist/mixxxlib
cp dist/mixxxlib ~/.local/bin/
mixxxlib info
```

On first run the zipapp unpacks itself into `~/.shiv/` and reuses that afterwards.

### Developer install

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/mixxxlib --help
```

## Safety rules

- **Mixxx must be closed.** Mixxx keeps an in-memory track cache that overwrites external
  edits; every mutating command checks for a running `mixxx` process and probes the database
  lock, and refuses with exit 5 if either says Mixxx is up. Read commands work while Mixxx is
  running and carry a `mixxx_running` warning.
- **Backups are automatic.** Before any database write, `VACUUM INTO` copies the database to
  `~/.local/share/mixxxlib/backups/mixxxdb-<UTC timestamp>-<command>.sqlite` (override with
  `--backup-dir` or `MIXXXLIB_BACKUP_DIR`). Backups older than 30 days are pruned, but the ten
  newest are always kept (`MIXXXLIB_BACKUP_TTL_DAYS`, `MIXXXLIB_BACKUP_KEEP_MIN`). Before any
  audio-file write, the file's tags are snapshotted to `tags-<timestamp>-<track id>.json` in the
  same directory. There is no `--no-backup`.
- **Foreign keys are broken upstream and must never be enabled.** Mixxx's schema references a
  table (`library_old`) that does not exist, so `PRAGMA foreign_keys = ON` would break a healthy
  database. mixxxlib never enables it and never alters the schema; referential checks are done in
  application code and surfaced by `doctor`.
- **Schema ceiling.** mixxxlib knows schema revisions 39 (Mixxx 2.5.x, verified) and 40
  (allowed with a `schema_unverified` warning). Anything above refuses to mutate with exit 6.
- Destructive commands need `--yes`. `--dry-run` prints the exact statements and file operations
  and changes nothing; it is never the default. `--yes` is checked after the crate/track/cue
  selector is resolved and right before the mutation runs, so a bad selector is always exit 3 or
  exit 4, never exit 8 — and `--dry-run` never needs `--yes`, on any command.

## Commands

Global options, accepted before or after the subcommand: `--settings-path DIR`, `--db FILE`,
`--backup-dir DIR`, `--json`. Most commands that write to the database or a file add `--dry-run`:
every `crate` subcommand that mutates, every `cue` subcommand that mutates, and `track set`. Of
those, the ones flagged destructive also require `--yes`: `crate delete`/`remove`/`set-tracks`,
`cue delete`/`clear`, and `track set` when it would write the audio file (a file-backed field
without `--mixxx-only`). `backup restore` is the one exception to the pattern: it requires
`--yes` but has no `--dry-run` at all. `backup create` and `backup prune` take neither — creating
a backup is never destructive, and prune only ever removes backups past the retention rule.

```sh
mixxxlib info                                   # paths, schema verdict, Mixxx running?, backups
mixxxlib settings show Library.SyncTrackMetadataExport

mixxxlib crate list --json
mixxxlib crate create "Peak Time"
mixxxlib crate add "Peak Time" 612 613 --json   # or: --paths /music/a.aiff, or --stdin
mixxxlib crate remove "Peak Time" 613 --yes
mixxxlib crate export "Peak Time" > peak.m3u8

mixxxlib track list --crate "Peak Time" --search "ashez"
mixxxlib track show 612 --json
mixxxlib track set 612 genre=Bass "comment=played 2026-10" --yes     # writes the file, then the database
mixxxlib track set 612 rating=5                                       # database-only field, no --yes needed
mixxxlib track set 612 --clear comment --yes

mixxxlib cue list 612
mixxxlib cue add 612 --type hotcue --slot 0 --position 61.5 --color '#C50A08' --label drop
mixxxlib cue clear 612 --type hotcue --yes

mixxxlib doctor --json
mixxxlib backup list
mixxxlib backup restore ~/.local/share/mixxxlib/backups/mixxxdb-20261008T142233Z-crate-add.sqlite --yes
```

Crates are addressed by name (`--id` for an integer id). Tracks are integer ids (`--paths` for
file paths). Cues are cue ids. `track set --clear FIELD` sets the database column to NULL and
removes the file tag — except `key`, where Mixxx's own default is `''`/`key_id 0` rather than
NULL, so `--clear key` writes that instead.

## JSON envelope and exit codes

Every successful command with `--json` prints exactly one object to stdout:

```json
{"ok": true, "command": "crate add", "data": {"crate": {"id": 7, "name": "Peak Time", "parent_id": null,
 "locked": false, "show": true, "autodj_source": false, "track_count": 2}, "added": [612, 613], "already_member": []},
 "warnings": [],
 "mutation": {"dry_run": false, "backup": "/home/me/.local/share/mixxxlib/backups/mixxxdb-20261008T142233Z-crate-add.sqlite",
              "integrity": "ok", "statements": 2}}
```

`warnings` is always present. `mutation` appears only on mutating commands; in a dry run
`statements` becomes the list of SQL statements (with bound values rendered) and file operations.
Errors go to stderr as `{"ok": false, "error": {"code", "exit_code", "message", "details"}}`
(or `mixxxlib: <code>: <message>` without `--json`). Stdout never carries anything but a
successful payload. Lists are sorted deterministically and no timestamps are invented, so the same
database produces byte-identical output.

| Code | Name | Meaning |
|---|---|---|
| 0 | `ok` | Success (warnings may be present) |
| 1 | `internal` | Unexpected exception; a bug |
| 2 | `usage` | Bad arguments or flags |
| 3 | `not_found` | Crate, track, cue, file or backup does not exist |
| 4 | `validation` | Input refused before any write (range, duplicate name, occupied slot, locked crate, a request whose fields no store in scope could write, …) |
| 5 | `mixxx_running` | Mixxx is running or the database is locked |
| 6 | `schema_unsupported` | Schema above the ceiling, below what a command needs, or unreadable |
| 7 | `database` | SQLite error, backup failure, or `integrity_check` not `ok` |
| 8 | `confirmation_required` | Destructive command without `--yes` |
| 9 | `tag_error` | Audio file missing, unsupported, or tags failed to read or write |
| 10 | `unhealthy` | `doctor` found at least one error-severity problem |

## Caveats: `bpm`, `key`, and `--mixxx-only`

`bpm` and `key` are writable because every metadata field is, but Mixxx derives them from the
`beats` and `keys` analysis blobs, which mixxxlib never writes. Mixxx will most likely resolve any
disagreement in the blob's favour and silently discard your value. Every write to these fields
succeeds with exit 0 and a `blob_authoritative` warning. Re-analyse the track in Mixxx if you need
the grid to move.

`track set` writes the audio file first, re-reads it to confirm, and only then updates the
database (and `source_synchronized_ms`), because that is the only order that stays consistent if
the process dies halfway. `--mixxx-only` skips the file. For file-backed fields (artist, title,
album, album artist, year, genre, track number/total, composer, grouping, comment, bpm, key) that
means your value lasts only until Mixxx next re-imports the file's tags; the command says so with a
`file_backed` warning. `--file-only` skips the database. A request whose fields have no counterpart
in whichever store is in scope — e.g. `--file-only rating=5`, since `rating` has no file tag — is
refused outright with exit 4 rather than partially applied.

`cue set` and `cue delete` do not surface beatgrid-decode warnings the way `cue list`, `cue add` and
`cue clear` do; and `doctor`'s `mixxx_running` check reports only the process probe — a database
already locked by Mixxx surfaces as exit 5 on the next mutating command instead of as a doctor
finding.

## Nested crates

Mixxx has no crate hierarchy today. An upstream draft (mixxxdj/mixxx PR #16871) adds
`crates.parent_id` at schema revision 41 and a `UNIQUE (parent_id, name)` rebuild at 42. mixxxlib
is shaped for it: every crate carries `parent_id` (always `null` for now), `crate tree`,
`crate move`, `--parent` and `--recursive` exist and return exit 6 below revision 41, and
sibling-name uniqueness is enforced in application code because SQLite treats `NULL` parents as
distinct. The ceiling stays at 40 until the PR merges and its SQL is verified against a real
migrated database.

## Path discovery

Highest precedence first: `--db FILE` / `--settings-path DIR`; `MIXXXLIB_DB` /
`MIXXXLIB_SETTINGS_PATH`; then the platform default settings directory (Linux `~/.mixxx`, macOS
`~/Library/Containers/org.mixxx.mixxx/Data/Library/Application Support/Mixxx` falling back to
`~/Library/Application Support/Mixxx`, Windows `%LOCALAPPDATA%\Mixxx`). The database is
`<settings>/mixxxdb.sqlite` unless `--db` says otherwise; `mixxx.cfg` is only needed by
`settings show`. Nothing is ever created implicitly; a missing database is exit 3.

## Library API

```python
from mixxxlib import Library

lib = Library.open()                        # or settings_path=..., db=..., backup_dir=...
crate = lib.crates.get("Peak Time")
result = lib.crates.add(crate, [612, 613])   # runs the full safety protocol, returns MutationResult
track, warnings = lib.tracks.get(612)        # Track plus any beatgrid-decode warnings
lib.tracks.set(track, {"genre": "Bass"}, file=True)
lib.cues.add(track, "hotcue", 61.5 * track.samples_per_second, 0xC50A08, slot=0)
findings = lib.doctor()
```

Errors are `mixxxlib.errors` subclasses with `.code`, `.exit_code` and `.details`.

## File structure

```
src/mixxxlib/
  cli/        Typer commands: backup, crate, cue, doctor, info, settings, track; common.py holds
              shared selector/confirmation/output helpers.
  db/         All SQL: connection (mutation plan + backup wiring), schema (revisions and
              capabilities), crates, cues, tracks.
  tags/       Audio-file tag reads/writes (files.py).
  blobs/      Mixxx's beats/keys analysis blob decoder (beats.py); read-only.
  safety/     The Mixxx-is-closed guard (guard.py) and the backup/retention/restore logic (backup.py).
  doctor.py, errors.py, library.py, model.py, output.py, paths.py, settings.py
              Doctor checks, the exit-code error hierarchy, the Library façade, frozen value
              types, text/JSON rendering, path resolution, mixxx.cfg parsing.
tests/
  unit/         Pure-function tests: no sandbox, no I/O beyond fixtures already on disk.
  integration/  CLI- and Library-level tests against a materialised copy of the fixtures.
  fixtures/     The committed Mixxx 2.5.6 database, audio, mixxx.cfg and manifests (see below).
scripts/
  build-zipapp.sh, check-schema-upstream.sh, sandbox-mixxx.sh, make-fixture.py
```

## Tests and the live harness

```sh
.venv/bin/pytest                      # fixture-based; never touches ~/.mixxx
scripts/check-schema-upstream.sh      # manual: does Mixxx main carry a revision above our ceiling?
scripts/sandbox-mixxx.sh 612          # manual, Linux: write a hotcue into a sandbox copy, launch Mixxx against it,
                                      # screenshot, stop, and diff the database
```

Fixtures live in `tests/fixtures/`: a real Mixxx 2.5.6 database trimmed to ~20 tracks with
one-second audio files, plus a derived schema-42 copy. Regenerate them with
`scripts/make-fixture.py` (see its docstring). Every integration test that exercises the sandbox
fixture ends by running `doctor` and asserting it found no new errors.
