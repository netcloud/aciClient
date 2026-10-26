"""Authentication configuration objects with redacted credential fields."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PasswordAuth:
    username: str
    password: str = field(repr=False)
    domain: str | None = None


@dataclass(frozen=True)
class CertificateAuth:
    certificate_name: str
    private_key: Path | bytes | Any = field(repr=False)
    certificate_dn: str
    private_key_password: str | bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class NexusDashboardPasswordAuth:
    username: str
    password: str = field(repr=False)
    domain: str = "local"


Auth = PasswordAuth | CertificateAuth | NexusDashboardPasswordAuth
