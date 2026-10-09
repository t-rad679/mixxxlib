"""Frozen value types returned by the library API (spec §6)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from mixxxlib.errors import ValidationError

# ---------------------------------------------------------------- musical key

# Mixxx ChromaticKey: 0 none, 1–12 majors, 13–24 minors (spec §6.2).
CHROMATIC_KEYS: dict[int, str] = {
    0: "", 1: "C", 2: "Db", 3: "D", 4: "Eb", 5: "E", 6: "F", 7: "Gb", 8: "G", 9: "Ab", 10: "A",
    11: "Bb", 12: "B", 13: "Cm", 14: "C#m", 15: "Dm", 16: "Ebm", 17: "Em", 18: "Fm", 19: "F#m",
    20: "Gm", 21: "G#m", 22: "Am", 23: "Bbm", 24: "Bm",
}
_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_KEY_RE = re.compile(r"^([A-Ga-g])([#♯b♭])?\s*(m|min|minor|maj|major)?$")


@dataclass(frozen=True)
class MusicalKey:
    id: int
    text: str

    @classmethod
    def parse(cls, value: str | int | None) -> "MusicalKey":
        if value is None:
            return cls(0, "")
        if isinstance(value, bool):
            raise ValidationError(f"unrecognised key {value!r}", key=value)
        if isinstance(value, int):
            if not 0 <= value <= 24:
                raise ValidationError(f"key_id {value} is outside 0..24", key_id=value)
            return cls(value, CHROMATIC_KEYS[value])
        text = str(value).strip()
        if text == "":
            return cls(0, "")
        if text.isdigit():
            return cls.parse(int(text))
        match = _KEY_RE.match(text)
        if not match:
            raise ValidationError(f"unrecognised key {text!r}", key=text)
        letter, accidental, mode = match.group(1).upper(), match.group(2), (match.group(3) or "")
        semitone = _SEMITONE[letter]
        if accidental in ("#", "♯"):
            semitone += 1
        elif accidental in ("b", "♭"):
            semitone -= 1
        semitone %= 12
        minor = mode in ("m", "min", "minor")
        return cls(13 + semitone if minor else 1 + semitone, text)

    @classmethod
    def from_db(cls, text: str | None, key_id: int | None) -> "MusicalKey":
        return cls(int(key_id or 0), text or "")

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text}


# ---------------------------------------------------------------- colours

def parse_color(value: str | int, *, allow_zero: bool = False) -> int:
    """Accept #RRGGBB, 0xRRGGBB, decimal, or 8-digit AARRGGBB (alpha stripped). Returns 24-bit RGB."""
    if isinstance(value, bool):
        raise ValidationError(f"colour must be a number or hex string, not {value!r}", color=value)
    if isinstance(value, int):
        number = value
    else:
        text = str(value).strip().lower()
        try:
            if text.startswith("#"):
                digits = text[1:]
                if len(digits) not in (6, 8):
                    raise ValueError
                number = int(digits, 16)
            elif text.startswith("0x"):
                number = int(text[2:], 16)
            elif text.isdigit():
                number = int(text)
            else:
                raise ValueError
        except ValueError:
            raise ValidationError(f"unrecognised colour {value!r}", color=value) from None
    if number < 0:
        raise ValidationError(f"colour {value!r} is negative", color=value)
    if number > 0xFFFFFF:
        number &= 0xFFFFFF  # strip the alpha byte of AARRGGBB
    if number == 0 and not allow_zero:
        raise ValidationError("colour must be non-zero; Mixxx discards cues whose colour is 0", color=value)
    return number


def format_color(value: int | None) -> str | None:
    if value is None:
        return None
    return f"#{value & 0xFFFFFF:06X}"


# ---------------------------------------------------------------- cue types

CUE_TYPES: dict[int, str] = {
    0: "invalid", 1: "hotcue", 2: "main", 3: "beat", 4: "loop", 5: "jump", 6: "intro", 7: "outro", 8: "n60db",
}
CUE_TYPE_IDS: dict[str, int] = {name: type_id for type_id, name in CUE_TYPES.items()}
WRITABLE_CUE_TYPES: frozenset[str] = frozenset({"hotcue", "loop", "jump", "intro", "outro"})
READ_ONLY_CUE_TYPES: frozenset[str] = frozenset(CUE_TYPES.values()) - WRITABLE_CUE_TYPES


# ---------------------------------------------------------------- crates

@dataclass(frozen=True)
class Crate:
    id: int
    name: str
    parent_id: int | None
    locked: bool
    show: bool
    autodj_source: bool
    track_count: int


@dataclass(frozen=True)
class CrateNode:
    crate: Crate
    children: tuple["CrateNode", ...] = ()


# ---------------------------------------------------------------- tracks

@dataclass(frozen=True)
class Location:
    id: int
    path: str | None
    filename: str | None
    directory: str | None
    filesize: int | None
    fs_deleted: bool


@dataclass(frozen=True)
class Metadata:
    artist: str | None
    title: str | None
    album: str | None
    album_artist: str | None
    year: str | None
    genre: str | None
    tracknumber: str | None
    tracktotal: str | None
    composer: str | None
    grouping: str | None
    comment: str | None
    rating: int | None
    color: int | None
    url: str | None
    bpm_lock: bool
    bpm: float | None
    key: MusicalKey


@dataclass(frozen=True)
class Audio:
    duration: float | None
    bitrate: int | None
    samplerate: int | None
    channels: int | None
    filetype: str | None
    replaygain: float | None
    replaygain_peak: float | None
    tuning_frequency_hz: float | None  # schema 40 only; None below


@dataclass(frozen=True)
class Bookkeeping:
    datetime_added: str | None
    played: bool
    timesplayed: int | None
    last_played_at: str | None
    mixxx_deleted: bool
    header_parsed: bool
    source_synchronized_ms: int | None


@dataclass(frozen=True)
class Beatgrid:
    version: str | None
    decoded: bool
    bpm: float | None = None
    first_beat_frame: int | None = None
    first_beat_seconds: float | None = None


@dataclass(frozen=True)
class Analysis:
    beats_version: str | None
    beats_sub_version: str | None
    keys_version: str | None
    keys_sub_version: str | None
    beatgrid: Beatgrid


@dataclass(frozen=True)
class CoverArt:
    source: int | None
    type: int | None
    location: str | None
    hash: int | None
    color: int | None


@dataclass(frozen=True)
class Track:
    id: int
    location: Location
    metadata: Metadata
    audio: Audio
    bookkeeping: Bookkeeping
    analysis: Analysis
    coverart: CoverArt

    @property
    def samples_per_second(self) -> float | None:
        """Interleaved samples per second: samplerate × channels (cue unit, spec §6.3)."""
        if not self.audio.samplerate or not self.audio.channels:
            return None
        return float(self.audio.samplerate * self.audio.channels)


# ---------------------------------------------------------------- cues

@dataclass(frozen=True)
class Cue:
    id: int
    track_id: int
    type_id: int
    position_samples: float
    length_samples: float
    slot: int
    label: str
    color: int
    position_seconds: float | None
    length_seconds: float | None

    @property
    def type(self) -> str:
        return CUE_TYPES.get(self.type_id, "unknown")


# ---------------------------------------------------------------- misc

@dataclass(frozen=True)
class WarningMessage:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str  # "error" | "warning" | "info"
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SchemaInfo:
    version: int | None
    raw: str | None
    ceiling: int
    capabilities: frozenset[str]
    verdict: str  # "supported" | "unverified" | "unsupported" | "missing"
    message: str


@dataclass(frozen=True)
class BackupEntry:
    path: str
    name: str
    kind: str  # "database" | "tags"
    size: int
    modified: str  # ISO 8601 UTC
    age_seconds: int


@dataclass
class MutationResult:
    data: dict[str, Any]
    statements: list[str]
    backup: str | None
    integrity: str | None
    dry_run: bool
    warnings: list[WarningMessage] = field(default_factory=list)
    file_operations: list[str] = field(default_factory=list)
