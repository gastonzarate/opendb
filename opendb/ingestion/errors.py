"""Errors safe to return through API/MCP boundaries."""


class IngestionError(ValueError):
    """Stable public error code, without SQL or submitted values."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def invalid(reason=None, *, path="operation"):
    message = "Invalid structured ingestion operation."
    if reason:
        message += f" {path}: {reason}."
    return IngestionError("invalid_operation", message)
