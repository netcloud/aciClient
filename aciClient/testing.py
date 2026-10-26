"""Lightweight deterministic helpers for consumer tests (never use the network)."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl

import httpx

from ._diagnostics import is_sensitive, redact
from .query import AciQuery, QueryValue, normalize_params


@dataclass(frozen=True)
class ExpectedRequest:
    method: str
    path: str
    query: (
        AciQuery | Sequence[tuple[str, QueryValue]] | Mapping[str, QueryValue] | None
    ) = None
    headers: Mapping[str, str] | None = None
    json: Any = None
    response: (
        httpx.Response
        | Mapping[str, Any]
        | Exception
        | Sequence[httpx.Response | Mapping[str, Any] | Exception]
    ) = field(default_factory=lambda: {"imdata": [], "totalCount": "0"})
    repeat: int = 1


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    query: tuple[tuple[str, str], ...]
    headers: Mapping[str, str]
    json: Any


def _redact(headers: httpx.Headers) -> dict[str, str]:
    return {k: "********" if is_sensitive(k) else v for k, v in headers.items()}


def _recorded_path(path: str) -> str:
    # ND embeds the APIC query in the proxy path rather than the URL query.
    base, separator, embedded_query = path.partition("?")
    if separator and is_sensitive(embedded_query):
        return base + "?[redacted]"
    return path


class ScriptedTransport(httpx.BaseTransport):
    def __init__(
        self, expectations: Sequence[ExpectedRequest] = (), *, ordered: bool = True
    ):
        self._expected: list[tuple[ExpectedRequest, int, int]] = [
            (x, x.repeat, 0) for x in expectations
        ]
        self.ordered = ordered
        self.history: list[RecordedRequest] = []
        self.closed = False
        self._lock = threading.RLock()

    def _handle_locked(self, request: httpx.Request) -> httpx.Response:
        content = request.read()
        parsed = None
        if content:
            try:
                parsed = json.loads(content)
            except ValueError:
                parsed = None
        query = tuple(
            parse_qsl(
                request.url.query.decode()
                if isinstance(request.url.query, bytes)
                else request.url.query,
                keep_blank_values=True,
            )
        )
        self.history.append(
            RecordedRequest(
                request.method,
                _recorded_path(request.url.path),
                tuple(
                    (
                        k,
                        "********"
                        if is_sensitive(k) or ("filter" in k and is_sensitive(v))
                        else v,
                    )
                    for k, v in query
                ),
                _redact(request.headers),
                redact(parsed),
            )
        )
        indexes = (
            range(min(1, len(self._expected)))
            if self.ordered
            else range(len(self._expected))
        )
        for index in indexes:
            expected, remaining, response_index = self._expected[index]
            if not self._matches(expected, request, query, parsed):
                continue
            responses = (
                expected.response
                if isinstance(expected.response, (list, tuple))
                else (expected.response,)
            )
            result = responses[min(response_index, len(responses) - 1)]
            if remaining <= 1:
                self._expected.pop(index)
            else:
                self._expected[index] = (expected, remaining - 1, response_index + 1)
            if isinstance(result, Exception):
                raise result
            if isinstance(result, httpx.Response):
                result.request = request
                return result
            return httpx.Response(200, json=result, request=request)
        raise AssertionError(
            f"unexpected request: {request.method} {_recorded_path(request.url.path)}"
        )

    @staticmethod
    def _matches(
        expected: ExpectedRequest,
        request: httpx.Request,
        query: tuple[tuple[str, str], ...],
        parsed: Any,
    ) -> bool:
        if (
            expected.method.upper() != request.method
            or expected.path != request.url.path
        ):
            return False
        if expected.query is not None:
            wanted = normalize_params(expected.query)
            if sorted(wanted) != sorted(query):
                return False
        if expected.headers and any(
            request.headers.get(k) != v for k, v in expected.headers.items()
        ):
            return False
        return expected.json is None or expected.json == parsed

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            return self._handle_locked(request)

    def close(self):
        self.closed = True

    def assert_all_consumed(self):
        if self._expected:
            raise AssertionError(
                f"{len(self._expected)} expected request(s) were not received"
            )


class ManualClock:
    def __init__(self, start: float = 0.0):
        self._time = float(start)
        self.sleep_calls: list[float] = []

    def monotonic(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("seconds cannot be negative")
        self._time += seconds

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.advance(seconds)


def managed_object(
    class_name: str,
    attributes: Mapping[str, Any],
    children: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    body = {"attributes": dict(attributes)}
    if children:
        body["children"] = list(children)
    return {class_name: body}


def imdata_response(
    *objects: Mapping[str, Any], total_count: int | None = None
) -> dict[str, Any]:
    return {
        "totalCount": str(len(objects) if total_count is None else total_count),
        "imdata": list(objects),
    }


def login_response(
    token: str = "test-token", version: str = "6.0(2h)", refresh_timeout: int = 600
) -> dict[str, Any]:
    return imdata_response(
        managed_object(
            "aaaLogin",
            {
                "token": token,
                "version": version,
                "refreshTimeoutSeconds": str(refresh_timeout),
            },
        )
    )


def api_error_response(
    code: str = "400", text: str = "request failed"
) -> dict[str, Any]:
    return imdata_response(managed_object("error", {"code": code, "text": text}))


def subscription_response(
    subscription_id: str | int = "1", *objects: Mapping[str, Any]
) -> dict[str, Any]:
    value = imdata_response(*objects)
    value["subscriptionId"] = subscription_id
    return value


def subscription_event(
    subscription_ids: str | int | Sequence[str | int], *objects: Mapping[str, Any]
) -> dict[str, Any]:
    return {"subscriptionId": subscription_ids, "imdata": list(objects)}


def snapshot_job_response(
    name: str, state: str = "success", dn: str | None = None, **attributes: Any
) -> dict[str, Any]:
    dn = (
        dn or f"uni/backupst/jobs-[uni/fabric/configexp-{name}]/run-2026-10-26T10-00-00"
    )
    return imdata_response(
        managed_object(
            "configJob",
            {
                "name": dn.rsplit("/run-", 1)[-1],
                "dn": dn,
                "operSt": state,
                **attributes,
            },
        )
    )


def snapshot_container_response(name: str, *jobs: Mapping[str, Any]) -> dict[str, Any]:
    # APIC emits relative names, rather than DNs, for nested configJob children.
    children = json.loads(json.dumps(jobs))
    for child in children:
        attrs = child["configJob"]["attributes"]
        if dn := attrs.pop("dn", None):
            attrs["rn"] = dn.rsplit("/", 1)[-1]
    return imdata_response(
        managed_object(
            "configJobCont",
            {
                "dn": f"uni/backupst/jobs-[uni/fabric/configexp-{name}]",
                "name": name,
                "pDn": f"uni/fabric/configexp-{name}",
                "lastJobName": children[-1]["configJob"]["attributes"]["name"]
                if children
                else "",
            },
            children,
        )
    )
