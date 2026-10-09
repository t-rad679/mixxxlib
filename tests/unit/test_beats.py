import sqlite3
import struct
from pathlib import Path

import pytest

from mixxxlib.blobs import beats
from mixxxlib.model import Beatgrid

FIXTURE_DB = Path(__file__).resolve().parents[1] / "fixtures" / "mixxxdb.sqlite"


def grid_blob(bpm: float, frame: int, extra: bytes = b"") -> bytes:
    bpm_msg = b"\x09" + struct.pack("<d", bpm)
    frame_varint = bytearray()
    value = frame
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            frame_varint.append(byte | 0x80)
        else:
            frame_varint.append(byte)
            break
    beat_msg = b"\x08" + bytes(frame_varint) + extra
    return b"\x0a" + bytes([len(bpm_msg)]) + bpm_msg + b"\x12" + bytes([len(beat_msg)]) + beat_msg


def test_read_varint():
    assert beats.read_varint(b"\x88\x02", 0) == (264, 2)
    assert beats.read_varint(b"\x01", 0) == (1, 1)
    with pytest.raises(beats.BlobError):
        beats.read_varint(b"\x80", 0)


def test_decode_known_blob_from_the_reference_library():
    blob = bytes.fromhex("0A090900000000004060401203088802")
    assert beats.decode_beatgrid_v2(blob) == (130.0, 264)


def test_decode_tolerates_extra_optional_fields():
    blob = grid_blob(145.0, 87, extra=b"\x10\x01\x18\x02")  # enabled=true, source=2
    assert beats.decode_beatgrid_v2(blob) == (145.0, 87)


def test_decode_rejects_truncated_blob():
    with pytest.raises(beats.BlobError):
        beats.decode_beatgrid_v2(bytes.fromhex("0A0909000000"))


def test_decode_dispatches_on_version():
    grid, warnings = beats.decode(grid_blob(128.0, 1234), "BeatGrid-2.0", 44100)
    assert grid == Beatgrid("BeatGrid-2.0", True, 128.0, 1234, 1234 / 44100) and warnings == []
    grid, warnings = beats.decode(b"\x00", "BeatMap-2.0", 44100)
    assert grid == Beatgrid("BeatMap-2.0", False) and warnings == []
    grid, warnings = beats.decode(b"\x00", "BeatSoup-9", 44100)
    assert grid == Beatgrid("BeatSoup-9", False) and [w.code for w in warnings] == ["unknown_blob_version"]
    assert beats.decode(None, None, 44100) == (Beatgrid(None, False), [])
    grid, warnings = beats.decode(b"\x0a\x00", "BeatGrid-2.0", 44100)
    assert grid.decoded is False and [w.code for w in warnings] == ["beats_undecodable"]


def test_frames_are_per_channel_but_cue_samples_are_interleaved():
    """Spec §6.4: beatgrid seconds = frame / samplerate; cue seconds = samples / (samplerate × channels)."""
    samplerate, channels = 44100, 2
    grid, _ = beats.decode(grid_blob(120.0, 44100), "BeatGrid-2.0", samplerate)
    assert grid.first_beat_seconds == 1.0
    cue_position_samples = 88200.0
    assert cue_position_samples / (samplerate * channels) == 1.0


def test_golden_every_fixture_blob_matches_library_bpm():
    conn = sqlite3.connect(f"{FIXTURE_DB.resolve().as_uri()}?mode=ro", uri=True)
    rows = conn.execute("SELECT id, bpm, beats, beats_version, samplerate FROM library WHERE beats IS NOT NULL").fetchall()
    assert len(rows) >= 10
    for track_id, bpm, blob, version, samplerate in rows:
        grid, warnings = beats.decode(blob, version, samplerate)
        assert grid.decoded, track_id
        assert grid.bpm == bpm, track_id
        assert warnings == []
