"""Public exception hierarchy for :mod:`aciClient`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ._diagnostics import redact


class AciClientError(Exception):
    """Base class for all modern client errors."""


class ConfigurationError(AciClientError):
    pass


class TransportError(AciClientError):
    pass


class AciTimeoutError(TransportError):
    pass


class AuthenticationError(AciClientError):
    pass


class AuthorizationError(AciClientError):
    pass


class QueryParameterError(AciClientError, ValueError):
    pass


class ResponseDecodeError(AciClientError, ValueError):
    pass


class ManagedObjectError(AciClientError):
    pass


class NoResultError(ManagedObjectError):
    pass


class MultipleResultsError(ManagedObjectError):
    pass


class AciJobError(AciClientError):
    pass


class JobTimeoutError(AciJobError):
    def __init__(self, message: str, *, raw: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.raw = redact(raw or {})


class AciApiError(AciClientError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        error_text: str | None = None,
        method: str | None = None,
        request_url: str | None = None,
        raw: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.error_text = error_text
        self.method = method
        self.request_url = request_url
        self.raw = redact(raw or {})


class JobFailedError(AciJobError):
    def __init__(
        self,
        message: str,
        *,
        job_dn: str | None = None,
        job_name: str | None = None,
        final_state: Any = None,
        error_code: str | None = None,
        error_text: str | None = None,
        raw: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.job_dn, self.job_name, self.final_state = job_dn, job_name, final_state
        self.error_code, self.error_text = error_code, error_text
        self.raw = redact(raw or {})
