"""Shared redaction for diagnostics and recorded test requests."""

from collections.abc import Mapping
from typing import Any


def is_sensitive(name: str) -> bool:
    """Recognize credential fields, including APIC shared keys and passwords."""
    normalized = name.lower().replace("-", "").replace("_", "")
    return normalized in {"authorization", "proxyauthorization", "userpass"} or any(
        word in normalized
        for word in (
            "password",
            "passwd",
            "pwd",
            "token",
            "secret",
            "key",
            "signature",
            "cookie",
        )
    )


def redact(value: Any) -> Any:
    """Copy structured diagnostic data while masking credential fields."""
    if isinstance(value, Mapping):
        return {
            name: "********" if is_sensitive(str(name)) else redact(item)
            for name, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    return value
