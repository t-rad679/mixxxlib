# mixxxlib — design spec

**Date:** 2026-10-08, revised 2026-10-09
**Status:** draft for review (revision 4)
**Sources:** the Trilium note tree under *mixxxlib* (*Initial mixxxlib Brainstorm*, *mixxxlib Interface Contract*, *Testing mixxxlib Against Real Mixxx*, *Driving Mixxx While It Is Running*, *Mixxx Has No Nested Crates*, *CLI Library Choice: absl-py vs Typer*), the linked *Beats Blob Decoder* component note, and the review notes on revision 1. Facts marked **[verified]** in those notes were measured against a Mixxx 2.5.6 library at schema 39 on 2026-10-08; nothing measured there is treated as an invariant of Mixxx in general. Upstream facts about nested crates were read from the Mixxx GitHub repository on 2026-10-09.

## 1. Purpose

`mixxxlib` is a deterministic, offline command-line tool and Python library for reading and writing a Mixxx library: the `mixxxdb.sqlite` database and the audio files' tags. It is consumed by programs first and people second. It holds no opinion about what any value means, never runs while Mixxx is running, and must never break the Mixxx database.

It fills a real gap: Mixxx 2.5.6 ships no library CLI at all, and existing third-party tools are narrow, read-mostly, and unmaintained.

**Name.** The tool is `mixxxlib`, not `mixxxctl`. The `ctl` suffix (systemctl, kubectl) means controlling a running process, which is the one thing this tool refuses to do; the name says what it touches instead. `mixxxctl` is left free for a future live-control sibling over virtual MIDI, which is the opposite contract (§1.1). The package, the import name, the command, the zipapp, the data directory (`~/.local/share/mixxxlib`) and the environment variables (`MIXXXLIB_*`) all share the one word.

### 1.1 Scope

**In v1**

- Crate CRUD, including membership and the `locked` / `show` / `autodj_source` flags.
- Track metadata: every user-facing metadata field readable and writable in the Mixxx database and, by default, in the audio file tags as well. `--mixxx-only` confines a write to the database (§7.4). Three fields are writable under a documented caveat (§6.4).
- Cue points: read and write.
- Beatgrid and key: read only.
- `doctor`: a pure-read health check that doubles as the regression harness.
- Mixxx settings introspection (`mixxx.cfg`).
- Automatic, pruned database backups before every mutation.

**Prepared for, ahead of upstream**

- **Nested crates.** Mixxx has no crate hierarchy today, but a live upstream PR adds one with a small, known schema change (§6.1). The v1 data model, command surface and schema guard are shaped so that supporting it is additive (§6.1, §8.2, §7.3). It is the first follow-on and outranks playlists.

**Follow-on (not in v1)**

- Playlist CRUD, after nested crates. The schema supports it, but playlists are ordered, inserts require renumbering, and the reference library has zero regular playlists. Nothing in v1 blocks adding it.

**Out of scope, permanently**

- Writing the `library.beats` and `library.keys` blobs. A malformed protobuf blob is the clearest route to breaking the database.
- Anything requiring Mixxx to be running, including live performance control over virtual MIDI. If wanted, that is a sibling component with the opposite contract.
- Any semantics: classification schemes, colour conventions, naming rules, crate-generation logic. Those live in the caller.

## 2. Standing constraints (the interface contract)

These apply to every command and every library call.

1. **The caller is a program.** `--json` is available on every command, with a stable, documented shape. Human output is a convenience layer, not the contract.
2. **Stable exit codes and machine-parseable errors** (§4). A caller branches on the code, never on prose.
3. **Never prompt, never require a TTY.** Anything that blocks waiting for a human is a bug.
4. **Destructive operations require `--yes`.** `--dry-run` prints the exact SQL statements and file operations and makes no change. Dry-run is never the default.
5. **Single-purpose, composable commands.** The caller sequences and compensates; `mixxxlib` never orchestrates multi-step workflows.
6. **Importable as a library.** All business logic is callable without the CLI layer, and importing the package registers nothing global.
7. **No semantics, only mechanism.** Values are validated for type, range and referential sanity, then written opaquely.
8. **No hardcoded paths, no assumptions about library content** — not the music root, not file formats, not which Mixxx features are in use.
9. **Degrade clearly on an unexpected schema.** Refuse to mutate above the supported ceiling and say why.
10. **Safety is part of the interface** (§7): refuse to run while Mixxx is up, back up before mutating, one transaction per command, verify integrity afterwards, never enable `PRAGMA foreign_keys`.

## 3. Technology and distribution

- **Python ≥ 3.12.** `sqlite3` is in the standard library; the beatgrid decoder is pure Python.
- **Typer** for the CLI. Decided. Subcommands are functions with typed parameters and nothing is process-global, which is what makes constraint 6 achievable. `absl-py` was rejected because its flags are global mutable state that would leak into any importer.
- **Runtime dependencies:** `typer`, `mutagen`. Nothing else. No `protobuf`, no ORM, no `rich`-dependent output.
- **Dev dependencies:** `pytest`, `shiv`.
- **Packaging:** a standard `pyproject.toml` with a `mixxxlib` console script and `python -m mixxxlib` entry point, `src/` layout. The developer install is `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`. No lockfile tooling is required.
- **Distribution artifact: a single-file zipapp.** `scripts/build-zipapp.sh` runs `shiv -c mixxxlib -o dist/mixxxlib .`, producing one executable file that bundles the package, Typer and mutagen behind a `#!/usr/bin/env python3` shebang. The user copies it anywhere on `PATH`, marks it executable and runs `mixxxlib …` like a binary; the only thing the target machine needs is Python ≥ 3.12. Both dependencies are pure Python, so one build serves every platform. On first run shiv unpacks the bundle into `~/.shiv/` and reuses it afterwards; `dist/` is git-ignored. A true interpreter-embedding binary (PyInstaller, Nuitka) is deliberately not built: it would need one build per OS and architecture for a gain that only matters on a machine with no Python at all.
- **Distribution target:** someone either downloads the zipapp or checks out the repo and builds it, then uses it against their own Mixxx install. A README is required (§11). No badges, CI matrix or contribution apparatus.
- **Schema reference, not vendored.** Mixxx's `res/schema.xml` is not copied into the repo. `schema.py` records a permalink to the file at the Mixxx 2.5.6 release commit (`94ccc34f`, which carries revision 39; `main` carries 40) and the table and column sets `mixxxlib` relies on, which are the real contract. `scripts/check-schema-upstream.sh` fetches `main` and prints any revision above the ceiling, which is the "visible diff" a vendored copy was meant to provide.
- **Scripts** that a human runs are POSIX `sh`. `bash` is used only where `sh` becomes ugly, and `zsh` only if `bash` does too. Anything with real logic is Python.

## 4. Exit codes and error shape

| Code | Name | Meaning |
|---|---|---|
| 0 | `ok` | Success. Warnings may be present (§5.2). |
| 1 | `internal` | Unexpected exception. Always a bug. |
| 2 | `usage` | Bad arguments or flags (Click's default). |
| 3 | `not_found` | A referenced crate, track, cue, file or backup does not exist. |
| 4 | `validation` | Input refused before any write: bad type or range, name too long, duplicate name among siblings, ambiguous crate name, occupied hotcue slot, locked crate, position outside track, unknown cue type, zero colour. |
| 5 | `mixxx_running` | Mixxx is running, or the database is locked by another connection. |
| 6 | `schema_unsupported` | Schema version above the ceiling, below the revision a command needs (nested-crate commands below 41), or the version row is missing or unreadable. |
| 7 | `database` | SQLite error, backup failure, or `integrity_check` not `ok` after commit. |
| 8 | `confirmation_required` | A destructive command was run without `--yes`. |
| 9 | `tag_error` | Audio file missing, unsupported format, or mutagen failed to read or write. |
| 10 | `unhealthy` | `doctor` ran successfully and found at least one error-severity problem. |

Codes 3–10 are raised before any write wherever possible. The only code that can follow a committed write is 7, and then the output names the backup taken before the write.

**Error output.** Errors always go to **stderr**; stdout never carries anything but a successful payload. With `--json`, stderr receives one JSON object:

```json
{"ok": false, "error": {"code": "validation", "exit_code": 4,
  "message": "hotcue slot 0 is already occupied on track 612 by cue 4251",
  "details": {"track_id": 612, "slot": 0, "existing_cue_id": 4251}}}
```

Without `--json`, stderr receives `mixxxlib: <code>: <message>`. `details` keys are documented per command and are part of the contract.

## 5. Output

### 5.1 JSON envelope

With `--json`, every successful command prints exactly one JSON object to stdout and nothing else:

```json
{"ok": true, "command": "crate add", "data": { ... },
 "warnings": [ {"code": "...", "message": "...", "details": { ... }} ],
 "mutation": {"dry_run": false, "backup": "/home/it/.local/share/mixxxlib/backups/mixxxdb-20261008T142233Z-crate-add.sqlite",
              "integrity": "ok", "statements": 3} }
```

- `data` has a fixed, documented shape per command (§8). Lists are always sorted deterministically (crates by name, tracks by id, cues by type then slot then position).
- `warnings` is always present, possibly empty.
- `mutation` is present only on mutating commands. In dry-run it carries `"dry_run": true` and `"statements"` becomes the list of SQL strings (with bound parameters rendered) and file operations that would have run, and `backup` is `null`.
- Numbers are numbers, nulls are `null`, booleans are booleans. SQLite `NULL` is never coerced to `""` or `0`.
- Key order is stable. No timestamps are added that are not stored in the database, so the same database produces byte-identical output.

### 5.2 Warnings

Warnings are for things the caller should know about a *successful* command. Exit code stays 0. Defined warnings:

| Code | When |
|---|---|
| `blob_authoritative` | `bpm` or `key` was written; the paired blob is authoritative and Mixxx may discard the write (§6.4). |
| `file_backed` | `--mixxx-only` wrote a field that Mixxx imports from the audio file; the file's value wins the next time Mixxx re-imports it (§6.2). |
| `mixxx_running` | A read command ran while Mixxx is up; values may be stale. |
| `no_file_tag` | A field was set that has no audio-file counterpart, so only the database changed. |
| `already_member` / `not_member` | `crate add` / `crate remove` was a no-op for some ids (idempotent). |
| `schema_unverified` | Schema is 40, which is allowed but has never been exercised against a real database. |

### 5.3 Human output

Without `--json`: plain-text tables and key/value blocks on stdout. Positions and lengths render as seconds; colours render as `#RRGGBB`. The format is unversioned and may change.

## 6. Data model

All types live in `mixxxlib.model` as frozen dataclasses and are what the library API returns.

### 6.1 Crate

**At schema ≤ 40 (every released Mixxx):** `crates`: `id`, `name` (varchar(48), unique, not null), `count`, `show`, `locked`, `autodj_source`. `crate_tracks`: `(crate_id, track_id)`, unique, **no position column** — crates are unordered sets with no hierarchy. **[verified]**

- `count` is vestigial; Mixxx 2.5 ignores it. `mixxxlib` never reads or writes it and reports a live `track_count` instead.
- Names are validated to 1–48 characters before insert; SQLite does not enforce the length but Mixxx's UI assumes it.
- A `locked` crate refuses membership changes and deletion with exit 4. The caller unlocks it first with `crate update --no-locked`. This mirrors what Mixxx's own UI enforces and is mechanism, not opinion.

**Nested crates, as upstream is building them.** The only live implementation is Mixxx PR #16871 (draft, opened 2026-08-10, based on `mixxxdj/proposals#21`, waiting on six foundational PRs). The older PR #1304 was closed unmerged in July 2020. #16871's schema change is two revisions:

- Revision 41: `ALTER TABLE crates ADD COLUMN parent_id INTEGER REFERENCES crates(id) DEFAULT NULL`.
- Revision 42: rebuild `crates` with `UNIQUE (parent_id, name)` in place of the unique `name`. Root crates have `parent_id = NULL`.

What v1 does about it now:

- The `Crate` model carries `parent_id: int | None` and every crate JSON shape includes `"parent_id"`, always `null` below schema 41. Callers never see the shape change.
- **Sibling uniqueness is enforced in application code.** SQLite treats `NULL` as distinct inside a UNIQUE constraint, so after revision 42 the database no longer prevents two root-level crates from sharing a name. `mixxxlib` refuses a duplicate name among siblings at every level, root included, with exit 4.
- **No path syntax, no reserved characters.** A crate is addressed by name or `--id`; once nesting exists, a name that matches crates under different parents is ambiguous (exit 4) and `--parent CRATE` disambiguates. Imposing a separator on crate names would be a naming rule, which belongs to the caller.
- Nested-crate commands exist in the surface (§8.2) and return exit 6 below schema 41. The schema ceiling stays at 40 until revisions 41 and 42 have been verified against a real database (§7.3), because the PR is a draft and its SQL may still change before merge.

### 6.2 Track

A track is a `library` row joined through `track_locations` on `library.location = track_locations.id`. `library.id == library.location` holds for every row in the reference library, but that is an artifact of lockstep inserts, not a guarantee. **Always join.** **[verified]**

**Metadata fields** (user-facing, readable and writable):

| Field | DB column | Type | File tag (ID3 / Vorbis) | Notes |
|---|---|---|---|---|
| `artist` | `artist` | text | `TPE1` / `ARTIST` | |
| `title` | `title` | text | `TIT2` / `TITLE` | |
| `album` | `album` | text | `TALB` / `ALBUM` | |
| `album_artist` | `album_artist` | text | `TPE2` / `ALBUMARTIST` | |
| `year` | `year` | text | `TDRC` / `DATE` | stored as text by Mixxx |
| `genre` | `genre` | text | `TCON` / `GENRE` | |
| `tracknumber` | `tracknumber` | text | `TRCK` / `TRACKNUMBER` | `TRCK` carries `n/total` |
| `tracktotal` | `tracktotal` | text | `TRCK` / `TRACKTOTAL` | Mixxx defaults this to the literal `//` |
| `composer` | `composer` | text | `TCOM` / `COMPOSER` | |
| `grouping` | `grouping` | text | `TIT1` (fallback `GRP1`) / `GROUPING` | a real round-tripped tag, not scratch space |
| `comment` | `comment` | text | `COMM` / `COMMENT` | |
| `rating` | `rating` | int 0–5 | — | database only |
| `color` | `color` | 24-bit RGB int, or null | — | database only; a real Mixxx 2.3 feature (schema rev 31), unused in the reference library |
| `url` | `url` | text | — | database only |
| `bpm_lock` | `bpm_lock` | bool | — | database only |
| `bpm` | `bpm` | float | `TBPM` / `BPM` | **caveat §6.4** |
| `key` | `key` + `key_id` | `MusicalKey` | `TKEY` / `INITIALKEY` | **caveat §6.4**; `key_id` is never set independently |

Fields with a file tag are **file-backed**. Mixxx re-imports a file's tags whenever the file's modification time is newer than `library.source_synchronized_ms`, so for these fields the audio file is the source of truth and the database is Mixxx's cache of it. A database-only write (`--mixxx-only`) to a file-backed field therefore lasts until the next time the file changes; the command says so with a `file_backed` warning. The default two-store write (§7.4) has no such caveat because it updates both and refreshes the sync timestamp.

**Read-only fields** (exposed in output, never written by `mixxxlib`): `id`, `location` (`id`, `path`, `filename`, `directory`, `filesize`, `fs_deleted`), `duration`, `bitrate`, `samplerate`, `channels`, `filetype`, `replaygain`, `replaygain_peak`, `datetime_added`, `played`, `timesplayed`, `last_played_at`, `mixxx_deleted`, `header_parsed`, `source_synchronized_ms`, `beats_version`, `beats_sub_version`, `keys_version`, `keys_sub_version`, cover-art columns, and `tuning_frequency_hz` where present (schema 40). This split — tag-like fields writable, bookkeeping and analysis read-only — is the interpretation of "all metadata fields" this spec commits to.

File-tag mapping by format: MP3 and AIFF carry ID3v2 (AIFF in its `ID3` chunk); FLAC carries Vorbis comments. The mapping above mirrors Mixxx's own import mapping; the `grouping` frames are verified from Mixxx source, the rest are the standard frames Mixxx's TagLib layer uses. Other formats mutagen can open are handled by the same table where the tag family matches, and refused with exit 9 otherwise.

`MusicalKey` is a single value object holding `key_id` (Mixxx `ChromaticKey`, 0 = none, 1–12 majors C, D♭, D, E♭, E, F, G♭, G, A♭, A, B♭, B, 13–24 minors C, C♯, D, E♭, E, F, F♯, G, G♯, A, B♭, B) and the display `key` text. It parses traditional notation (`Em`, `E minor`, `Ebm`, `Ab`) and bare `key_id` integers; it always writes both columns so they cannot drift. **[verified]** that writing one without the other desynchronises the library view.

### 6.3 Cue

`cues`: `id`, `track_id`, `type`, `position`, `length`, `hotcue`, `label`, `color`. **[verified]**

- `type` (from upstream `cueinfo.h`): 0 Invalid, 1 HotCue, 2 MainCue, 3 Beat (unused), 4 Loop, 5 Jump, 6 Intro, 7 Outro, 8 N60dBSound (analyzer-owned, hidden). Exposed by name in JSON (`hotcue`, `main`, `loop`, `jump`, `intro`, `outro`, `n60db`), with the integer alongside.
- `position` and `length` are **interleaved stereo samples**: `seconds = value / (samplerate × channels)`. Declared `INTEGER` but **store `REAL` in real data**; the reader always treats them as `float`. Output carries both `position_samples` and `position_seconds`. **[verified]**
- `hotcue` is the 0-based slot in the database (1-based in the UI); `-1` for non-hotcue types. There is **no uniqueness constraint on `(track_id, type, hotcue)`**; Mixxx resolves duplicates by dropping one arbitrarily, so `mixxxlib` enforces uniqueness itself. **[verified]**
- `color`: a hotcue whose colour is `0` is silently discarded by Mixxx on load. Every cue write requires an explicit non-zero colour. **[verified]**
- **Colour representation.** Rows Mixxx wrote itself store 24-bit RGB (`0xFF8000` for every loop in the reference library); the harness wrote `0xFF`-prefixed 32-bit values and Mixxx rendered them correctly. `mixxxlib` stores what Mixxx stores — 24-bit RGB, 1–0xFFFFFF — and accepts input as `#RRGGBB`, `0xRRGGBB`, decimal, or 8-digit `AARRGGBB` with the alpha byte stripped. The first integration test against live Mixxx asserts both forms render identically.
- **Writable types in v1:** `hotcue`, `loop`, `jump`, `intro`, `outro`. `main` is read-only until the relationship between a MainCue row and `library.cuepoint` is verified; `n60db`, `beat` and `invalid` are never written.

### 6.4 Beatgrid, key blob, and the three caveated fields

`library.beats` is a protobuf blob whose format is named in `beats_version`. `BeatGrid-2.0` is one `double` (bpm) and one `varint` (`first_beat.frame_position`), 16 bytes. The vendored decoder (`mixxxlib.blobs.beats`) reads it with no protobuf dependency; it decoded 566/566 blobs in the reference library with every BPM matching `library.bpm` exactly. **[verified]**

- Dispatch on `beats_version`; `BeatGrid-1.0`, `BeatMap-1.0`, `BeatMap-2.0` are recognised by name and reported as `{"version": ..., "decoded": false}` in v1; unknown versions likewise, with a warning. Nothing is guessed.
- **Units:** `frame_position` is per-channel frames, `seconds = frame_position / samplerate`. This differs from cues by a factor of `channels`. The conversion is covered by an explicit test.
- `library.keys` (`KeyMap-1.0`) is not decoded in v1; `keys_version` is reported and key information comes from the `key`/`key_id` columns.
- **`bpm`, `key` and `key_id` stay writable**, because all metadata fields are writable, but the blob is authoritative: the grid and the column move together and Mixxx will most likely resolve a disagreement in the blob's favour, silently discarding the write. Every write to these fields succeeds with exit 0 **and** a `blob_authoritative` warning in JSON and a one-line note on stderr. The README repeats the caveat.

### 6.5 Settings

`mixxx.cfg` is plain INI. `settings show` parses it into `{section: {key: value}}`. No setting changes `mixxxlib` behaviour in v1; `SyncTrackMetadataExport` is surfaced because a caller may want to know whether Mixxx will propagate database edits to files on its own (in the reference library it is `0`: it will not).

## 7. Safety protocol

### 7.1 Facts the protocol is built on **[verified]**

- The schema's foreign keys are broken upstream: `crate_tracks.track_id`, `cues.track_id` and `PlaylistTracks.track_id` reference a non-existent `library_old`; `library.location` references the text column `track_locations(location)`; `track_analysis.track_id` is declared against `track_locations(id)` but holds a `library.id`. `PRAGMA foreign_key_check` reports thousands of violations on a healthy database. **`mixxxlib` never enables `PRAGMA foreign_keys` and never alters the DDL.** Referential integrity is enforced in application code.
- `journal_mode = delete`. An exclusive writer blocks readers too, so `BEGIN IMMEDIATE` with a short timeout is a reliable lock probe.
- Running Mixxx holds many connections and an in-memory track cache that overwrites external edits. The hazard of editing live is the cache, not the file lock.
- Launching Mixxx writes to the database by itself (a new history playlist per launch).

### 7.2 Read protocol

Every read command opens `file:<path>?mode=ro` so it is incapable of mutation. Reads are permitted while Mixxx is running; the output then carries a `mixxx_running` warning. A locked database yields exit 5.

### 7.3 Mutation protocol

Every mutating command, in this order:

1. **Resolve** the database path (§9) and open a read-write connection with a 1-second busy timeout.
2. **Schema guard.** Read `settings.value WHERE key = 'mixxx.schema.version'` and look it up in the capability table in `schema.py`, which maps each known revision to what it enables (`nested_crates` from 41, the `UNIQUE (parent_id, name)` rebuild from 42) and names the ceiling, 40. Above the ceiling, missing or unparsable → exit 6, naming the version found. Exactly 40 → proceed with a `schema_unverified` warning. A command that needs a capability the version lacks → exit 6, naming the revision it needs. Supporting a new revision means adding a row to the table and raising the ceiling after verification, not editing a constant in several places.
3. **Mixxx-running guard.** Best-effort process check (`pgrep -x mixxx` where available), then the authoritative probe: `BEGIN IMMEDIATE` then `ROLLBACK`. Either failing → exit 5.
4. **Validate** every input against the database inside a read transaction: ids exist, names fit, sibling names unique, slots free, positions inside the track, colours non-zero, crate not locked. Any failure → exit 4 with details. Nothing has been written yet.
5. **Confirmation.** If the command is destructive (§7.5) and `--yes` is absent → exit 8.
6. **Dry-run.** If `--dry-run`, print the statements and file operations and exit 0. No backup, no write.
7. **Backup** with `VACUUM INTO '<backup path>'` — never `cp`, which can copy a torn page set. Failure → exit 7, nothing written.
8. **File tags first**, if the command writes them and `--mixxx-only` was not given (§7.4).
9. **One transaction:** `BEGIN IMMEDIATE`, all statements, `COMMIT`. Any error → `ROLLBACK`, exit 7.
10. **Verify:** `PRAGMA integrity_check`. Anything but `ok` → exit 7 with the backup path in the error details. Otherwise report the result in `mutation.integrity`.
11. **Prune** old backups (§7.6).

### 7.4 Two-store writes

For a file-backed field, `track set` writes both stores by default. The only order that stays consistent if the process dies halfway is **file → re-read to confirm → database**, because a crash after the file write leaves the database merely stale, which Mixxx's own re-import repairs, whereas database-first leaves the database asserting something the file does not.

1. Snapshot the file's current tag values to `<backup dir>/tags-<timestamp>-<track id>.json`.
2. Write the tags with mutagen; re-open the file and confirm the values read back. Mismatch → restore from the snapshot, exit 9.
3. Run the database transaction (§7.3 step 9), which also sets `library.source_synchronized_ms` to the file's new modification time in epoch milliseconds, so Mixxx does not conclude the file is unchanged and keep serving stale values.
4. If the database step fails after the file was written, restore the file tags from the snapshot (best effort) and report both outcomes in the exit-7 error details.

**`--mixxx-only`** skips the file entirely and writes only the database. It is the flag for callers that leave audio files to another tool. For file-backed fields it adds the `file_backed` warning (§6.2); for database-only fields it changes nothing. **`--file-only`** skips the database (and the backup). Fields with no file counterpart are database-only automatically, with a `no_file_tag` warning.

Mutagen is used directly rather than Mixxx's own export because Mixxx's MP3 tag export is reported broken since 2.5.0 and because Mixxx is closed anyway.

### 7.5 What counts as destructive

`--yes` is required for any command that **deletes rows** or **writes to an audio file**. Database field updates and inserts do not require it, because the automatic backup makes them reversible; audio files have only the tag snapshot. Concretely: `crate delete`, `crate remove`, `crate set-tracks`, `cue delete`, `cue clear`, `backup restore`, and `track set` unless `--mixxx-only`.

### 7.6 Backups

- Location: `$XDG_DATA_HOME/mixxxlib/backups/` (default `~/.local/share/mixxxlib/backups/`), overridable with `--backup-dir` or `MIXXXLIB_BACKUP_DIR`.
- Name: `mixxxdb-<UTC ISO 8601 basic, e.g. 20261008T142233Z>-<command with spaces as hyphens>.sqlite`.
- Retention: TTL of 30 days (`MIXXXLIB_BACKUP_TTL_DAYS`) with a floor of the 10 most recent (`MIXXXLIB_BACKUP_KEEP_MIN`), so a long gap can never leave zero backups. Tag snapshots follow the same rule.
- There is no `--no-backup`. The sandbox harness redirects with `--backup-dir`.

## 8. Command surface

Global options, accepted before or after the subcommand:

| Option | Meaning |
|---|---|
| `--settings-path DIR` | Mixxx settings directory; database is `DIR/mixxxdb.sqlite`, config is `DIR/mixxx.cfg`. |
| `--db FILE` | Database file directly. Overrides `--settings-path` for the database; `mixxx.cfg` is then optional. |
| `--backup-dir DIR` | See §7.6. |
| `--json` | Machine output (§5.1). |

Mutating commands additionally accept `--dry-run` and `--yes`.

**Selectors.** Crates are addressed by name; `--id` makes the selector an integer id. Under nested crates a name that matches crates under different parents is ambiguous (exit 4) and `--parent CRATE` narrows it; there is no path syntax and no reserved character. Tracks are integer ids; `--paths` makes them file paths resolved through `track_locations.location` after `os.path.abspath`. Cues are cue ids.

### 8.1 `info` and `settings`

- `info` — resolved paths, schema version, capabilities and verdict, whether Mixxx is running, backup directory, `mixxxlib` version. Pure read. The first thing a caller or a human runs.
- `settings show [SECTION[.KEY]]` — contents of `mixxx.cfg`.

### 8.2 `crate`

| Command | Effect | `data` |
|---|---|---|
| `crate list [--parent CRATE \| --root]` | All crates; with nesting, optionally one level. | `[{id, name, parent_id, locked, show, autodj_source, track_count}]` |
| `crate show CRATE [--tracks] [--recursive]` | One crate, optionally with its tracks; `--recursive` includes descendants' tracks (schema ≥ 41), matching Mixxx's recursive view. | `{crate, tracks?}` |
| `crate tree` | The hierarchy as nested objects (schema ≥ 41); a flat list of roots below. | `[{crate, children: [...]}]` |
| `crate create NAME [--parent CRATE]` | Insert; duplicate name among siblings → exit 4. `--parent` needs schema ≥ 41. | `{crate}` |
| `crate rename CRATE NEW_NAME` | | `{crate}` |
| `crate move CRATE --parent CRATE \| --root` | Re-parent a crate and its subtree (schema ≥ 41). Refuses a cycle with exit 4. | `{crate}` |
| `crate update CRATE [--locked/--no-locked] [--show/--no-show] [--autodj-source/--no-autodj-source]` | Flags only. | `{crate}` |
| `crate delete CRATE --yes [--recursive]` | Deletes the crate and its `crate_tracks` rows. A crate with children is refused (exit 4) unless `--recursive`, which deletes the subtree. | `{deleted: [{crate, track_count}]}` |
| `crate add CRATE TRACK... [--paths] [--stdin]` | Idempotent membership add; unknown track → exit 4 before any write. | `{crate, added: [ids], already_member: [ids]}` |
| `crate remove CRATE TRACK... --yes [--paths] [--stdin]` | Idempotent removal. | `{crate, removed: [ids], not_member: [ids]}` |
| `crate set-tracks CRATE [TRACK...] --yes [--paths] [--stdin]` | Make membership exactly this set. | `{crate, added, removed, unchanged}` |
| `crate export CRATE [--format m3u8] [--recursive]` | `#EXTM3U` with `#EXTINF:<seconds>,<artist> - <title>` lines and absolute paths, to stdout; a correctness oracle against Mixxx's own exporter. | playlist text on stdout; `--json` is ignored |

`--stdin` reads one selector per line; the only way to pass thousands of ids portably. Membership is per crate: a parent crate's own `crate_tracks` rows are what `add`, `remove` and `set-tracks` touch, and `--recursive` on the read side is how a caller sees the union, exactly as Mixxx shows it.

### 8.3 `track`

| Command | Effect | `data` |
|---|---|---|
| `track list [--crate CRATE] [--search TEXT]` | All tracks, or those in a crate, or those whose artist/title/album contains TEXT (case-insensitive `LIKE`, escaped). | `[{track}]` |
| `track show TRACK` | One track with analysis summary and cues. | `{track, beatgrid, cues}` |
| `track set TRACK FIELD=VALUE... [--clear FIELD]... [--mixxx-only \| --file-only] [--yes]` | Write metadata (§7.4). `--clear` sets NULL in the database and removes the tag from the file. `--yes` is needed only when the file is written. | `{track, changed: {field: {old, new}}, file_written: bool}` |

Track shape:

```json
{"id": 612,
 "location": {"id": 612, "path": "/music/a.aiff", "filename": "a.aiff", "directory": "/music",
              "filesize": 41234567, "fs_deleted": false},
 "metadata": {"artist": "...", "title": "...", "album": null, "album_artist": null, "year": "2019",
              "genre": null, "tracknumber": null, "tracktotal": "//", "composer": null, "grouping": null,
              "comment": null, "rating": 0, "color": null, "url": null, "bpm_lock": false,
              "bpm": 128.0, "key": {"id": 17, "text": "Em"}},
 "audio": {"duration": 195.4, "bitrate": 1411, "samplerate": 44100, "channels": 2, "filetype": "aiff",
           "replaygain": 0.0, "replaygain_peak": 0.0},
 "bookkeeping": {"datetime_added": "2026-09-30T12:00:00", "played": false, "timesplayed": 0,
                 "last_played_at": null, "mixxx_deleted": false, "header_parsed": true,
                 "source_synchronized_ms": 1727697600000},
 "analysis": {"beats_version": "BeatGrid-2.0", "beats_sub_version": "rounding=V4|vamp_plugin_id=qm-tempotracker:0",
              "keys_version": "KeyMap-1.0",
              "beatgrid": {"version": "BeatGrid-2.0", "decoded": true, "bpm": 128.0,
                           "first_beat_frame": 1234, "first_beat_seconds": 0.02798}}}
```

Cue shape (inside `track show` and `cue list`):

```json
{"id": 4251, "track_id": 612, "type": "hotcue", "type_id": 1, "slot": 0,
 "position_samples": 88200.0, "position_seconds": 1.0, "length_samples": 0.0, "length_seconds": 0.0,
 "label": "drop", "color": 12913160, "color_hex": "#C50A08"}
```

### 8.4 `cue`

| Command | Effect | `data` |
|---|---|---|
| `cue list TRACK [--type TYPE]` | | `[{cue}]` |
| `cue add TRACK --type TYPE --position SECONDS\|--position-samples N --color C [--length ...] [--slot N] [--label TEXT]` | Validated insert; `--slot` required for `hotcue`, forbidden otherwise. | `{cue}` |
| `cue set CUE [--position ...] [--length ...] [--slot N] [--label TEXT] [--color C]` | Update; same validation. | `{cue, changed: {...}}` |
| `cue delete CUE --yes` | | `{deleted: cue}` |
| `cue clear TRACK [--type TYPE] --yes` | Delete every writable-type cue on a track, or only those of one writable type. Read-only types (`main`, `n60db`, `beat`, `invalid`) are never touched. | `{deleted: [cue]}` |

Validation for every cue write: type in the writable set (§6.3); colour non-zero 24-bit; slot unique per `(track, type)` and 0-based; `0 ≤ position` and `position + length ≤ duration × samplerate × channels`; `length ≥ 0`. Positions given in seconds are converted with the track's own `samplerate` and `channels`.

### 8.5 `doctor`

Pure read. Runs every check, prints findings, exits 0 when clean, 10 when any error-severity finding exists, and 5/6/7 only if it could not run at all.

| Check | Severity | What |
|---|---|---|
| `schema` | error above the ceiling, warning at 40 | version, capabilities and verdict |
| `mixxx_running` | info | process and lock probe |
| `integrity` | error | `PRAGMA integrity_check` |
| `orphans` | error | semantic FK scan, since real FK checking is unusable: `crate_tracks` → `crates`/`library`; `cues` → `library`; `PlaylistTracks` → `Playlists`/`library`; `library.location` → `track_locations.id`; `track_analysis.track_id` → `library.id`; and from schema 41, `crates.parent_id` → `crates.id` plus a cycle check |
| `crates` | warning | duplicate names among siblings (from schema 42, where the database no longer catches the root level) |
| `missing_files` | warning | `fs_deleted = 1`, or `location` that does not exist on disk |
| `unanalyzed` | info | `beats IS NULL` or `bpm = 0` |
| `cues` | warning | hotcues with colour 0, duplicate `(track, type, slot)`, positions beyond duration, unknown types |
| `keys` | warning | `key` text and `key_id` disagree |
| `blob_versions` | warning | `beats_version` / `keys_version` the reader does not recognise |
| `backups` | info | backup dir, count, newest, oldest |

`data`: `{"findings": [{"check", "severity", "message", "details"}], "summary": {"errors": n, "warnings": n, "info": n}}`.

### 8.6 `backup`

| Command | Effect |
|---|---|
| `backup list` | Backups and tag snapshots with size and age. |
| `backup create [--label TEXT]` | Manual `VACUUM INTO`; the label replaces the command name in the file name. |
| `backup prune` | Apply the retention rule now. |
| `backup restore FILE --yes` | Runs the full mutation guard (Mixxx closed, lock probe), backs up the current database first, checks the candidate with `integrity_check`, then copies it into place. |

## 9. Path discovery

Database and config resolution, highest precedence first:

1. `--db FILE` (database) and `--settings-path DIR` (config and, if `--db` absent, database).
2. `MIXXXLIB_DB`, `MIXXXLIB_SETTINGS_PATH`.
3. Platform default settings directory: Linux `~/.mixxx`; macOS `~/Library/Containers/org.mixxx.mixxx/Data/Library/Application Support/Mixxx`, falling back to `~/Library/Application Support/Mixxx`; Windows `%LOCALAPPDATA%\Mixxx`.

A database that does not exist at the resolved path → exit 3, with the path in the details. Nothing is ever created implicitly.

## 10. Code structure

`src/` layout; the CLI is a thin shell over a library that never imports Typer.

```
src/mixxxlib/
  __init__.py          # __version__, Library re-export
  __main__.py          # python -m mixxxlib
  model.py             # Crate (with parent_id), Track, Cue, Beatgrid, MusicalKey, Finding (frozen dataclasses)
  errors.py            # MixxxctlError(code, exit_code, details) and subclasses, one per §4 row
  paths.py             # settings/db/backup resolution (§9)
  library.py           # Library.open(...) — the public API façade
  db/
    connection.py      # open_ro(), mutation() context manager implementing §7.3
    schema.py          # capability table by revision, SCHEMA_CEILING = 40, SCHEMA_SOURCE permalink, column sets
    crates.py          # all SQL for crates, sibling-uniqueness and cycle checks
    tracks.py          # all SQL for library + track_locations
    cues.py            # all SQL for cues, slot/position validation
  blobs/
    beats.py           # varint/double decoder, version dispatch
  tags/
    files.py           # mutagen read/write, the §6.2 mapping, snapshot/restore
  safety/
    guard.py           # process check, lock probe, schema guard
    backup.py          # VACUUM INTO, naming, retention
  doctor.py            # the §8.5 checks
  output.py            # JSON envelope, human rendering, colour/seconds formatting
  cli/
    __init__.py        # root Typer app, global options, error → exit code mapping
    crate.py  track.py  cue.py  doctor.py  backup.py  settings.py
scripts/
  build-zipapp.sh          # shiv -c mixxxlib -o dist/mixxxlib . (POSIX sh)
  sandbox-mixxx.sh         # §12.3 live harness (POSIX sh)
  check-schema-upstream.sh # fetch main's schema.xml, print revisions above the ceiling (POSIX sh)
  make-fixture.py          # trims a real database copy into tests/fixtures
dist/                      # build output, git-ignored
tests/
docs/superpowers/specs/
README.md  pyproject.toml
```

### 10.1 Library API

```python
from mixxxlib import Library

lib = Library.open(settings_path=None, db=None, backup_dir=None)
lib.crates.list(parent=None) -> list[Crate]
lib.crates.get(name_or_id, parent=None) -> Crate
lib.crates.tree() -> list[CrateNode]
lib.crates.create(name, *, parent=None) -> Crate
lib.crates.move(crate, *, parent) -> MutationResult          # schema >= 41
lib.crates.add(crate, track_ids, *, dry_run=False) -> MutationResult
lib.tracks.list(crate=None, search=None, recursive=False) -> list[Track]
lib.tracks.get(id_or_path) -> Track
lib.tracks.set(track, fields: dict, *, file=True, dry_run=False) -> MutationResult   # file=False is --mixxx-only
lib.cues.list(track) -> list[Cue]
lib.cues.add(track, type, position_samples, color, *, slot=None, length=0.0, label="") -> MutationResult
lib.doctor() -> list[Finding]
lib.settings -> dict[str, dict[str, str]]
lib.schema -> SchemaInfo   # version, capabilities, ceiling
```

Every mutating method runs the full §7.3 protocol and returns a `MutationResult` (`backup`, `integrity`, `statements`, `warnings`, plus the command-specific payload). Errors are the `errors.py` subclasses; `confirmation_required` exists only in the CLI, because a library caller has already decided. The CLI maps each exception class to its exit code in exactly one place. `Library.open` accepts a `schema_ceiling` override for tests only; the CLI never exposes it.

## 11. README

Required sections, in this order: what it does and does not do; install, with the zipapp first (download or build `dist/mixxxlib`, `chmod +x`, put it on `PATH`; Python ≥ 3.12 is the only requirement) and the editable venv second as the developer path; the safety rules (Mixxx must be closed; backups are automatic and where they live; foreign keys are broken upstream and must never be enabled; the schema ceiling); the command surface with one example per group; the JSON envelope and exit-code table; the three caveated fields and the `--mixxx-only` caveat; nested crates: what is prepared and what waits on upstream; path discovery; running the tests and the live harness.

## 12. Testing

### 12.1 Principles

- Never touch the live database. Every test runs against a copy; read-only probes use `mode=ro`.
- Fixtures come from a real library copy, not a hand-built schema, because the schema's fossils and type surprises are exactly what must be exercised.
- Assert against the database, not logs. Mixxx silently discards malformed data.

### 12.2 Layers

**Committed fixtures.** `tests/fixtures/mixxxdb.sqlite`: a real database trimmed by `scripts/make-fixture.py` to about twenty tracks, with `track_locations` rewritten to point at `tests/fixtures/audio/` — one-second silent AIFF, MP3 and FLAC files with known tags. The fixture keeps the original DDL verbatim, including the broken foreign-key declarations, fractional cue positions, a `color = 0` hotcue, a duplicate hotcue slot, a track with `key`/`key_id` out of step, and an unanalyzed track, so `doctor` has something to find. A second fixture, `mixxxdb-42.sqlite`, is derived from the first by applying revisions 41 and 42 exactly as PR #16871 writes them, with a three-level crate tree and two root crates sharing a name; it is regenerated when the PR's SQL changes.

**Unit tests** (no database): beatgrid decoder golden test against every blob in the fixture (BPM must equal `library.bpm`); the factor-of-two test asserting a known cue's seconds and a known beatgrid's seconds explicitly; `MusicalKey` parsing and round-trip; colour parsing; exit-code mapping; path precedence; the capability table.

**Integration tests** (fresh copy of a fixture per test): every command, both output modes; every validation rejection in §8.4 and §6.1; the schema guard refusing 41 on the ceiling of 40 and naming it; nested-crate commands returning exit 6 on the schema-39 fixture and working on the schema-42 fixture with the test-only ceiling override, including sibling uniqueness at the root and cycle refusal; the lock probe refusing while another connection holds `BEGIN EXCLUSIVE`; backup creation, naming and retention floor; two-store write order, including a simulated database failure after the file write; `--mixxx-only` leaving the file untouched and emitting `file_backed`; `--dry-run` producing no backup and no change. **Every integration test ends by running `doctor` and asserting no new errors.**

**Zipapp smoke test:** build `dist/mixxxlib` with `scripts/build-zipapp.sh` into a temporary directory and run `info --json` and `doctor --json` through it against the schema-39 fixture, asserting the envelope parses and the exit codes match the venv run. This is the only test that touches the distribution artifact, and it is what catches a dependency that shiv cannot bundle.

**Schema pin:** the schema-39 fixture's tables and columns equal the column sets in `schema.py`. `scripts/check-schema-upstream.sh` is a manual, network-using check and is not part of the test run.

### 12.3 Live Mixxx harness (manual, Linux)

`scripts/sandbox-mixxx.sh` reproduces the verified recipe: recreate `/tmp/mixxx-sandbox`, copy the database and `mixxx.cfg` in, apply the test writes with `mixxxlib --settings-path /tmp/mixxx-sandbox --backup-dir /tmp/mixxx-sandbox/backups`, launch `QT_QPA_PLATFORM=xcb mixxx --settings-path /tmp/mixxx-sandbox --safe-mode <track>`, screenshot via `xdotool` and `import`, `SIGTERM`, then diff the sandbox database (row values, ids **and** `sqlite_sequence`) against the pre-launch snapshot. Known noise: ALSA errors, a missing `samplers.xml` warning, and one new history row in `Playlists` per launch. A `SIGTERM` result proves Mixxx did not clobber the rows, not that it re-saved them on a clean shutdown; that remains an open question.

The harness has already established that externally written hotcues load, render in the written colours and survive byte-identical, that `color = 0` hotcues are discarded, and that duplicate slots are resolved by dropping one.

## 13. Open items carried forward

These are known and deliberately not blocking v1:

- Clean-shutdown cue behaviour in Mixxx is unproven (the harness could not trigger a graceful quit under the compositor).
- Schema 40 is allowed sight-unseen; its only migration is one additive nullable column. Re-confirm on the first Mixxx release that ships it.
- PR #16871 is a draft. Revisions 41 and 42 are prepared for as written today; when the PR merges, re-read its SQL, regenerate the schema-42 fixture, verify against a real migrated database, then raise the ceiling.
- Whether Mixxx masks the alpha byte of a 32-bit colour or rejects it is settled by the first live harness run (§6.3).
- MainCue writes wait on verifying the `library.cuepoint` mirror.
- Playlist CRUD follows nested crates.
