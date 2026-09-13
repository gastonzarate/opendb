"""Errors safe to return through API/MCP boundaries."""


class IngestionError(ValueError):
    """Stable public error code, without SQL or submitted data."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def invalid():
    return IngestionError(
        "invalid_operation", "Invalid structured ingestion operation."
    )
