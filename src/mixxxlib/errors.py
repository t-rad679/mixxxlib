"""Error hierarchy. One subclass per exit-code row in spec §4."""

from __future__ import annotations

from typing import Any


class MixxxlibError(Exception):
    """Base for every error mixxxlib raises on purpose."""

    code: str = "internal"
    exit_code: int = 1

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "exit_code": self.exit_code,
                "message": self.message,
                "details": self.details,
            },
        }


class InternalError(MixxxlibError):
    code = "internal"
    exit_code = 1


class UsageError(MixxxlibError):
    code = "usage"
    exit_code = 2


class NotFoundError(MixxxlibError):
    code = "not_found"
    exit_code = 3


class ValidationError(MixxxlibError):
    code = "validation"
    exit_code = 4


class MixxxRunningError(MixxxlibError):
    code = "mixxx_running"
    exit_code = 5


class SchemaUnsupportedError(MixxxlibError):
    code = "schema_unsupported"
    exit_code = 6


class DatabaseError(MixxxlibError):
    code = "database"
    exit_code = 7


class ConfirmationRequiredError(MixxxlibError):
    code = "confirmation_required"
    exit_code = 8


class TagError(MixxxlibError):
    code = "tag_error"
    exit_code = 9


class UnhealthyError(MixxxlibError):
    code = "unhealthy"
    exit_code = 10


ERROR_CLASSES: tuple[type[MixxxlibError], ...] = (
    InternalError, UsageError, NotFoundError, ValidationError, MixxxRunningError,
    SchemaUnsupportedError, DatabaseError, ConfirmationRequiredError, TagError, UnhealthyError,
)

EXIT_CODES: dict[str, int] = {"ok": 0, **{cls.code: cls.exit_code for cls in ERROR_CLASSES}}
