"""Decoder for Mixxx's `library.beats` blob (spec §6.4). Pure Python; no protobuf dependency.

BeatGrid-2.0 is `message BeatGrid { Bpm bpm = 1; Beat first_beat = 2; }` with
`Bpm { double bpm = 1; }` and `Beat { int32 frame_position = 1; bool enabled = 2; Source source = 3; }`.
`frame_position` is in per-channel frames: seconds = frame_position / samplerate.
"""

from __future__ import annotations

import struct
from typing import Any, Iterator

from mixxxlib.model import Beatgrid, WarningMessage

KNOWN_VERSIONS: frozenset[str] = frozenset({"BeatGrid-1.0", "BeatGrid-2.0", "BeatMap-1.0", "BeatMap-2.0"})


class BlobError(ValueError):
    """The blob does not parse as the protobuf message its version claims."""


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise BlobError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, pos
        if shift > 70:
            raise BlobError("varint too long")


def iter_fields(buf: bytes) -> Iterator[tuple[int, int, Any]]:
    """Yield (field_number, wire_type, value) for each top-level field in a protobuf message."""
    pos = 0
    while pos < len(buf):
        tag, pos = read_varint(buf, pos)
        field_number, wire_type = tag >> 3, tag & 7
        if wire_type == 0:
            value, pos = read_varint(buf, pos)
        elif wire_type == 1:
            value, pos = buf[pos:pos + 8], pos + 8
        elif wire_type == 2:
            length, pos = read_varint(buf, pos)
            value, pos = buf[pos:pos + length], pos + length
        elif wire_type == 5:
            value, pos = buf[pos:pos + 4], pos + 4
        else:
            raise BlobError(f"unsupported wire type {wire_type}")
        if pos > len(buf):
            raise BlobError("field runs past end of blob")
        yield field_number, wire_type, value


def _int32(value: int) -> int:
    return value - (1 << 32) if value >= 1 << 31 else value


def decode_beatgrid_v2(blob: bytes) -> tuple[float, int]:
    bpm: float | None = None
    frame: int | None = None
    for number, wire_type, value in iter_fields(blob):
        if number == 1 and wire_type == 2:
            for sub_number, sub_type, sub_value in iter_fields(value):
                if sub_number == 1 and sub_type == 1 and len(sub_value) == 8:
                    bpm = struct.unpack("<d", sub_value)[0]
        elif number == 2 and wire_type == 2:
            for sub_number, sub_type, sub_value in iter_fields(value):
                if sub_number == 1 and sub_type == 0:
                    frame = _int32(sub_value)
    if bpm is None or frame is None:
        raise BlobError("BeatGrid-2.0 blob lacks bpm or first_beat.frame_position")
    return bpm, frame


def decode(blob: bytes | None, version: str | None, samplerate: int | None) -> tuple[Beatgrid, list[WarningMessage]]:
    """Dispatch on beats_version. Nothing is guessed: only BeatGrid-2.0 is decoded in v1."""
    if blob is None or version is None:
        return Beatgrid(version, False), []
    if version == "BeatGrid-2.0":
        try:
            bpm, frame = decode_beatgrid_v2(bytes(blob))
        except BlobError as exc:
            return Beatgrid(version, False), [WarningMessage("beats_undecodable", str(exc), {"version": version})]
        seconds = frame / samplerate if samplerate else None
        return Beatgrid(version, True, bpm, frame, seconds), []
    if version in KNOWN_VERSIONS:
        return Beatgrid(version, False), []
    return Beatgrid(version, False), [
        WarningMessage("unknown_blob_version", f"beats_version {version!r} is not recognised", {"version": version})
    ]
