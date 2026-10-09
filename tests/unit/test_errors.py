import pytest

from mixxxlib import errors


@pytest.mark.parametrize(
    "cls, code, exit_code",
    [
        (errors.InternalError, "internal", 1),
        (errors.UsageError, "usage", 2),
        (errors.NotFoundError, "not_found", 3),
        (errors.ValidationError, "validation", 4),
        (errors.MixxxRunningError, "mixxx_running", 5),
        (errors.SchemaUnsupportedError, "schema_unsupported", 6),
        (errors.DatabaseError, "database", 7),
        (errors.ConfirmationRequiredError, "confirmation_required", 8),
        (errors.TagError, "tag_error", 9),
        (errors.UnhealthyError, "unhealthy", 10),
    ],
)
def test_each_error_maps_to_its_exit_code(cls, code, exit_code):
    err = cls("boom", track_id=7)
    assert err.code == code
    assert err.exit_code == exit_code
    assert err.message == "boom"
    assert err.details == {"track_id": 7}
    assert str(err) == "boom"
    assert isinstance(err, errors.MixxxlibError)
    assert errors.EXIT_CODES[code] == exit_code


def test_exit_code_table_is_complete():
    assert errors.EXIT_CODES == {
        "ok": 0, "internal": 1, "usage": 2, "not_found": 3, "validation": 4,
        "mixxx_running": 5, "schema_unsupported": 6, "database": 7,
        "confirmation_required": 8, "tag_error": 9, "unhealthy": 10,
    }


def test_to_payload_shape():
    err = errors.ValidationError("slot 0 occupied", track_id=612, slot=0)
    assert err.to_payload() == {
        "ok": False,
        "error": {"code": "validation", "exit_code": 4, "message": "slot 0 occupied",
                  "details": {"track_id": 612, "slot": 0}},
    }
