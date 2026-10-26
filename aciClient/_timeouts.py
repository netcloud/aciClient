"""Carry an operation's remaining time through nested synchronous requests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import httpx

from .exceptions import AciTimeoutError
from .runtime import SyncRuntime

_active_deadline: ContextVar[Deadline | None] = ContextVar(
    "aci_request_deadline", default=None
)


class Deadline:
    """A monotonic operation budget, local to the calling thread or context."""

    def __init__(self, runtime: SyncRuntime, timeout: float):
        self._runtime = runtime
        self._expires_at = runtime.monotonic() + timeout

    @property
    def remaining(self) -> float:
        return max(0.0, self._expires_at - self._runtime.monotonic())

    @contextmanager
    def activate(self) -> Iterator[None]:
        outer = _active_deadline.get()
        selected = (
            outer if outer is not None and outer.remaining < self.remaining else self
        )
        token = _active_deadline.set(selected)
        try:
            yield
        finally:
            _active_deadline.reset(token)


def remaining_request_time() -> float | None:
    deadline = _active_deadline.get()
    if deadline is None:
        return None
    remaining = deadline.remaining
    if remaining <= 0:
        raise AciTimeoutError("operation deadline exceeded")
    return remaining


def bounded_timeout(timeout: httpx.Timeout) -> httpx.Timeout:
    """Cap each HTTP phase, including pool acquisition, at the remaining budget."""
    remaining = remaining_request_time()
    if remaining is None:
        return timeout
    return httpx.Timeout(
        **{
            phase: remaining if value is None else min(value, remaining)
            for phase, value in timeout.as_dict().items()
        }
    )
