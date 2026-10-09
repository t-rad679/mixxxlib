import json

from mixxxlib import output
from mixxxlib.errors import ValidationError
from mixxxlib.model import Crate, Cue, MusicalKey, MutationResult, WarningMessage


def test_as_json_shapes():
    crate = Crate(1, "A", None, False, True, False, 3)
    assert output.as_json(crate) == {"id": 1, "name": "A", "parent_id": None, "locked": False, "show": True,
                                     "autodj_source": False, "track_count": 3}
    assert output.as_json(MusicalKey(17, "Em")) == {"id": 17, "text": "Em"}
    cue = Cue(4251, 612, 1, 88200.0, 0.0, 0, "drop", 12913160, 1.0, 0.0)
    assert output.as_json(cue) == {"id": 4251, "track_id": 612, "type": "hotcue", "type_id": 1, "slot": 0,
                                   "position_samples": 88200.0, "position_seconds": 1.0, "length_samples": 0.0,
                                   "length_seconds": 0.0, "label": "drop", "color": 12913160, "color_hex": "#C50A08"}
    assert output.as_json({"crate": crate, "ids": (1, 2), "s": frozenset({3, 1})}) == \
        {"crate": output.as_json(crate), "ids": [1, 2], "s": [1, 3]}


def test_envelope_without_and_with_mutation():
    env = output.envelope("crate list", [], [WarningMessage("mixxx_running", "stale", {})])
    assert env == {"ok": True, "command": "crate list", "data": [],
                   "warnings": [{"code": "mixxx_running", "message": "stale", "details": {}}]}
    result = MutationResult(data={"x": 1}, statements=["UPDATE a", "UPDATE b"], backup="/b.sqlite", integrity="ok", dry_run=False)
    env = output.envelope("crate add", result.data, [], result)
    assert env["mutation"] == {"dry_run": False, "backup": "/b.sqlite", "integrity": "ok", "statements": 2}
    dry = MutationResult(data={}, statements=["UPDATE a"], backup=None, integrity=None, dry_run=True, file_operations=["write x"])
    assert output.envelope("track set", {}, [], dry)["mutation"] == {"dry_run": True, "backup": None, "integrity": None,
                                                                      "statements": ["UPDATE a", "write x"]}


def test_dumps_is_deterministic_and_unicode_safe():
    text = output.dumps({"b": 1, "a": "é"})
    assert text == '{"b": 1, "a": "é"}'
    assert json.loads(text) == {"b": 1, "a": "é"}


def test_error_text():
    assert output.error_text(ValidationError("bad slot", slot=0)) == "mixxxlib: validation: bad slot"


def test_table_and_kv():
    rows = [{"id": 1, "name": "A", "locked": False}, {"id": 22, "name": None, "locked": True}]
    assert output.table(rows, ["id", "name", "locked"]).splitlines() == ["id  name  locked", "1   A     no", "22        yes"]
    assert output.kv({"a": 1, "b": {"c": None, "d": 1.5}}).splitlines() == ["a: 1", "b:", "  c: ", "  d: 1.5"]
