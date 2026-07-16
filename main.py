"""Compatibility wrapper for the canonical application entrypoint."""

from app.entrypoint import (
    flush_logs,
    initialize_database,
    main,
    validate_startup_configuration,
)

__all__ = [
    "flush_logs",
    "initialize_database",
    "main",
    "validate_startup_configuration",
]


if __name__ == "__main__":
    main()
