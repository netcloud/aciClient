"""Immutable public value objects used by the APIC client."""

from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from functools import total_ordering
from types import MappingProxyType
from typing import Any

from ._diagnostics import redact
from .exceptions import MultipleResultsError, NoResultError, ResponseDecodeError


def _copy_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _copy_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_value(item) for item in value)
    return deepcopy(value)


def _freeze_map(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(_copy_value(value or {}))


@total_ordering
@dataclass(frozen=True)
class ApicVersion:
    major: int
    minor: int
    patch: int | None = None
    build: str | None = field(default=None, compare=False)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ApicVersion):
            return NotImplemented
        return (
            self.major,
            self.minor,
            -1 if self.patch is None else self.patch,
        ) < (
            other.major,
            other.minor,
            -1 if other.patch is None else other.patch,
        )


@dataclass(frozen=True)
class ConnectionInfo:
    apic_version: ApicVersion
    apic_version_raw: str
    via_nexus_dashboard: bool
    nexus_dashboard_cluster_name: str | None


@dataclass(frozen=True)
class ConnectionIdentity:
    endpoint: str
    principal: str
    authentication_type: str
    domain: str | None
    nexus_dashboard_cluster_name: str | None
    tls_profile: str | None
    proxy_profile: str | None


@dataclass(frozen=True)
class SessionInfo:
    authenticated: bool
    principal: str
    authenticated_at: datetime | None = None
    refresh_deadline: datetime | None = None
    maximum_lifetime_deadline: datetime | None = None
    last_refresh_at: datetime | None = None


@dataclass(frozen=True)
class LoginDomain:
    name: str
    display_name: str | None
    realm: str | None
    is_default: bool
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "raw", _freeze_map(self.raw))


@dataclass(frozen=True)
class LoginDomains:
    domains: tuple[LoginDomain, ...]
    default_domain: str | None
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "raw", _freeze_map(self.raw))


@dataclass(frozen=True, repr=False)
class ManagedObject:
    class_name: str
    attributes: Mapping[str, Any]
    children: tuple["ManagedObject", ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "attributes", _freeze_map(self.attributes))
        object.__setattr__(self, "children", tuple(self.children))
        object.__setattr__(self, "raw", _freeze_map(self.raw or self.to_imdata()))

    def __repr__(self) -> str:
        attrs = redact(self.attributes)
        return f"ManagedObject(class_name={self.class_name!r}, attributes={attrs!r}, children={self.children!r})"

    @classmethod
    def from_imdata(cls, record: Mapping[str, Any]) -> "ManagedObject":
        if not isinstance(record, Mapping) or len(record) != 1:
            raise ResponseDecodeError(
                "an imdata entry must contain exactly one class key"
            )
        class_name, body = next(iter(record.items()))
        if not isinstance(body, Mapping):
            raise ResponseDecodeError("managed-object body must be an object")
        attributes = body.get("attributes", {})
        if not isinstance(attributes, Mapping):
            raise ResponseDecodeError("managed-object attributes must be an object")
        children_raw = body.get("children", ())
        if not isinstance(children_raw, (list, tuple)):
            raise ResponseDecodeError("managed-object children must be a list")
        return cls(
            str(class_name),
            dict(attributes),
            tuple(cls.from_imdata(child) for child in children_raw),
            dict(record),
        )

    @classmethod
    def create(
        cls,
        class_name: str,
        attributes: Mapping[str, Any],
        children: tuple["ManagedObject", ...] = (),
    ) -> "ManagedObject":
        return cls(class_name, dict(attributes), tuple(children))

    def to_imdata(self) -> dict[str, Any]:
        body: dict[str, Any] = {"attributes": dict(self.attributes)}
        if self.children:
            body["children"] = [child.to_imdata() for child in self.children]
        return deepcopy({self.class_name: body})

    @property
    def dn(self) -> str | None:
        value = self.attributes.get("dn")
        return value if isinstance(value, str) else None

    def children_of(self, class_name: str) -> tuple["ManagedObject", ...]:
        return tuple(c for c in self.children if c.class_name == class_name)

    def first_child(self, class_name: str) -> "ManagedObject | None":
        return next((c for c in self.children if c.class_name == class_name), None)

    def require_child(self, class_name: str) -> "ManagedObject":
        child = self.first_child(class_name)
        if child is None:
            raise NoResultError(f"no child of class {class_name!r}")
        return child


@dataclass(frozen=True)
class AciResponse:
    objects: tuple[ManagedObject, ...]
    raw_imdata: tuple[Mapping[str, Any], ...] = field(repr=False)
    total_count: int | None
    status_code: int
    headers: Mapping[str, str] = field(repr=False)
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "objects", tuple(self.objects))
        object.__setattr__(
            self, "raw_imdata", tuple(_freeze_map(x) for x in self.raw_imdata)
        )
        object.__setattr__(self, "headers", _freeze_map(self.headers))
        object.__setattr__(self, "raw", _freeze_map(self.raw))

    def first(self) -> ManagedObject | None:
        return self.objects[0] if self.objects else None

    def one(self) -> ManagedObject:
        if not self.objects:
            raise NoResultError("response contains no objects")
        if len(self.objects) != 1:
            raise MultipleResultsError(f"response contains {len(self.objects)} objects")
        return self.objects[0]

    @property
    def is_empty(self) -> bool:
        return not self.objects

    def objects_of(self, class_name: str) -> tuple[ManagedObject, ...]:
        return tuple(x for x in self.objects if x.class_name == class_name)

    def to_dict(self) -> dict[str, Any]:
        return _copy_value(self.raw)


@dataclass(frozen=True)
class HttpConnectionLimits:
    max_connections: int = 20
    max_keepalive_connections: int = 10
    keepalive_expiry: float | None = 30.0
    pool_timeout: float | None = None

    def __post_init__(self):
        if self.max_connections <= 0 or self.max_keepalive_connections < 0:
            raise ValueError(
                "connection limits must be non-negative and max_connections positive"
            )
        if self.max_keepalive_connections > self.max_connections:
            raise ValueError("keep-alive connections cannot exceed max connections")
        if self.keepalive_expiry is not None and self.keepalive_expiry < 0:
            raise ValueError("keepalive_expiry cannot be negative")
        if self.pool_timeout is not None and self.pool_timeout <= 0:
            raise ValueError("pool_timeout must be positive")


@dataclass(frozen=True)
class PaginationPolicy:
    page_size: int = 1000
    max_pages: int | None = None
    max_items: int | None = None
    deadline: float | None = None

    def __post_init__(self):
        if self.page_size <= 0:
            raise ValueError("page_size must be positive")
        if self.max_pages is not None and self.max_pages <= 0:
            raise ValueError("max_pages must be positive")
        if self.max_items is not None and self.max_items <= 0:
            raise ValueError("max_items must be positive")
        if self.deadline is not None and (
            not math.isfinite(self.deadline) or self.deadline < 0
        ):
            raise ValueError("deadline must be finite and non-negative")


@dataclass(frozen=True)
class SubscriptionInitialResponse:
    subscription_id: str
    objects: tuple[ManagedObject, ...]
    raw_imdata: tuple[Mapping[str, Any], ...] = field(repr=False)
    total_count: int | None
    raw: Mapping[str, Any] = field(repr=False)


class JobState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SnapshotJobInfo:
    policy_dn: str
    job_dn: str | None
    name: str
    description: str
    target_dn: str | None
    submitted_at: datetime | None
    state: JobState
    attributes: Mapping[str, Any] = field(repr=False)
    raw: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True)
class SnapshotResult:
    job: SnapshotJobInfo
    snapshot_name: str | None
    completed_at: datetime | None
    error_code: str | None
    error_text: str | None
    raw: Mapping[str, Any] = field(repr=False)
    warnings: tuple[str, ...] = ()
