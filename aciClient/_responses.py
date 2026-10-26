"""Decode APIC and Nexus Dashboard responses into public value objects."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import httpx

from .exceptions import (
    AciApiError,
    AuthenticationError,
    AuthorizationError,
    ResponseDecodeError,
)
from .models import AciResponse, ApicVersion, LoginDomain, LoginDomains, ManagedObject

_VERSION_RE = re.compile(
    r"(?P<major>\d+)\.(?P<minor>\d+)(?:\((?P<patch>\d+)(?P<build>[^)]*)\))?"
)


def parse_version(raw: str) -> ApicVersion:
    match = _VERSION_RE.search(raw or "")
    if not match:
        raise ResponseDecodeError(f"unable to parse APIC version {raw!r}")
    patch = int(match.group("patch")) if match.group("patch") else None
    build = (match.group("build") or "").lstrip(".,- ") or None
    return ApicVersion(
        int(match.group("major")), int(match.group("minor")), patch, build
    )


def _json_object(
    response: httpx.Response, *, allow_empty: bool = False
) -> Mapping[str, Any]:
    if allow_empty and not response.content:
        return {}
    try:
        raw = response.json()
    except ValueError as exc:
        raise ResponseDecodeError("response is not valid JSON") from exc
    if not isinstance(raw, Mapping):
        raise ResponseDecodeError("response must be an object")
    return raw


def _response_error(raw: Mapping[str, Any]) -> tuple[str | None, str | None]:
    for item in imdata_records(raw):
        if not isinstance(item, Mapping):
            continue
        if "error" in item:
            error = item["error"]
            if not isinstance(error, Mapping):
                raise ResponseDecodeError("APIC error must be an object")
            body = error.get("attributes", {})
            if not isinstance(body, Mapping):
                raise ResponseDecodeError("APIC error attributes must be an object")
            return (
                str(body.get("code") or "") or None,
                str(body.get("text") or "APIC request failed"),
            )
    code = raw.get("code", raw.get("statusCode"))
    error = raw.get("error")
    if (isinstance(error, str) and error) or (
        str(code).isdigit() and int(str(code)) >= 400
    ):
        return str(code) if code is not None else None, str(
            raw.get("message") or error or raw.get("description") or "request failed"
        )
    return None, None


def _raise_api_error(
    response: httpx.Response,
    method: str,
    path: str,
    raw: Mapping[str, Any],
    error_code: str | None,
    error_text: str | None,
) -> None:
    if not 200 <= response.status_code < 300 or error_code or error_text:
        if response.status_code == 401:
            raise AuthenticationError(error_text or "authentication failed")
        if response.status_code == 403:
            raise AuthorizationError(error_text or "authorization failed")
        error_text = error_text or raw.get("message") or raw.get("description")
        message = error_text or f"APIC request failed with HTTP {response.status_code}"
        raise AciApiError(
            message,
            status_code=response.status_code,
            error_code=error_code,
            error_text=error_text,
            method=method,
            request_url=path,
            raw=raw,
        )


def decode_response(response: httpx.Response, method: str, path: str) -> AciResponse:
    raw = checked_json(response, method, path)
    imdata = imdata_records(raw)
    objects = tuple(ManagedObject.from_imdata(item) for item in imdata)
    total = raw.get("totalCount")
    try:
        total_count = int(total) if total is not None else None
    except (TypeError, ValueError, OverflowError) as exc:
        raise ResponseDecodeError("totalCount must be an integer") from exc
    return AciResponse(
        objects,
        tuple(dict(x) for x in imdata),
        total_count,
        response.status_code,
        dict(response.headers),
        dict(raw),
    )


def checked_json(response: httpx.Response, method: str, path: str) -> Mapping[str, Any]:
    try:
        raw = _json_object(response, allow_empty=True)
        code, message = _response_error(raw)
    except ResponseDecodeError:
        _raise_api_error(response, method, path, {}, None, None)
        raise
    _raise_api_error(response, method, path, raw, code, message)
    return raw


def imdata_records(raw: Mapping[str, Any]) -> list[Any]:
    records = raw.get("imdata", [])
    if not isinstance(records, list):
        raise ResponseDecodeError("imdata must be a list")
    return records


def decode_subscription_id(raw: Mapping[str, Any]) -> str:
    """Require a usable subscription identifier instead of leaking KeyError."""
    value = raw.get("subscriptionId")
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or not str(value).strip()
    ):
        raise ResponseDecodeError("subscription response must contain a subscriptionId")
    return str(value)


def decode_login(
    raw: Mapping[str, Any], gateway: bool
) -> tuple[Mapping[str, Any], str | None, str | None]:
    """Validate the platform-specific login envelope and credential types."""
    values: Mapping[str, Any] = raw if gateway else {}
    if not gateway:
        for entry in imdata_records(raw):
            if not isinstance(entry, Mapping):
                raise ResponseDecodeError("login record must be an object")
            if "aaaLogin" not in entry:
                continue
            login = entry["aaaLogin"]
            if not isinstance(login, Mapping):
                raise ResponseDecodeError("aaaLogin must be an object")
            values = login.get("attributes", {})
            if not isinstance(values, Mapping):
                raise ResponseDecodeError("login attributes must be an object")
            if values:
                break
    token = (
        (values.get("jwttoken") or values.get("token"))
        if gateway
        else values.get("token")
    )
    version = None if gateway else values.get("version") or values.get("apicVersion")
    if token is not None and not isinstance(token, str):
        raise ResponseDecodeError("authentication token must be a string")
    if version is not None and not isinstance(version, str):
        raise ResponseDecodeError("APIC version must be a string")
    return values, token, version


def decode_domains(raw: Mapping[str, Any], gateway: bool) -> LoginDomains:
    result = []
    default = str(raw["defaultDomain"]) if raw.get("defaultDomain") else None
    if gateway:
        entries = raw.get("domains") or raw.get("items") or raw.get("data") or []
        if isinstance(entries, Mapping):
            entries = entries.get("items", [])
        if not isinstance(entries, list):
            raise ResponseDecodeError("login domains must be a list")
        for item in entries:
            if not isinstance(item, Mapping):
                raise ResponseDecodeError("login domain must be an object")
            name = str(item.get("name") or item.get("id") or "")
            is_default = name == default or str(
                item.get("isDefault") or item.get("default") or ""
            ).lower() in {"yes", "true", "1"}
            if is_default:
                default = name
            result.append(
                LoginDomain(
                    name, item.get("displayName"), item.get("realm"), is_default, item
                )
            )
    else:
        for entry in imdata_records(raw):
            if not isinstance(entry, Mapping):
                raise ResponseDecodeError("login domain must be an object")
            if "name" in entry or "domain" in entry:
                attrs = entry
            else:
                body = next(iter(entry.values()), {})
                if not isinstance(body, Mapping):
                    raise ResponseDecodeError("invalid login domain record")
                attrs = body.get("attributes", {})
                if not isinstance(attrs, Mapping):
                    raise ResponseDecodeError(
                        "login domain attributes must be an object"
                    )
            name = str(attrs.get("name") or attrs.get("domain") or "")
            is_default = str(attrs.get("isDefault", "")).lower() in {"yes", "true", "1"}
            if is_default:
                default = name
            result.append(
                LoginDomain(
                    name,
                    attrs.get("guiBanner") or attrs.get("displayName"),
                    attrs.get("realm"),
                    is_default,
                    entry,
                )
            )
    return LoginDomains(tuple(result), default, raw)
