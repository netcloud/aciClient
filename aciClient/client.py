"""Modern synchronous Cisco APIC client."""

from __future__ import annotations

import json
import re
import ssl
import warnings
from collections.abc import Iterator, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx

from ._responses import (
    checked_json,
    decode_domains,
    decode_response,
    decode_subscription_id,
    parse_version,
)
from ._session import AuthSession, validate_auth
from ._timeouts import Deadline, bounded_timeout, remaining_request_time
from .auth import Auth, CertificateAuth
from .exceptions import (
    AciTimeoutError,
    ConfigurationError,
    ResponseDecodeError,
    TransportError,
)
from .models import (
    AciResponse,
    ConnectionIdentity,
    ConnectionInfo,
    HttpConnectionLimits,
    JobState,
    LoginDomains,
    ManagedObject,
    PaginationPolicy,
    SessionInfo,
    SnapshotJobInfo,
    SubscriptionInitialResponse,
)
from .query import AciQuery, QueryParams, QueryValue, normalize_params
from .runtime import SyncRuntime, SystemSyncRuntime
from .snapshots import SnapshotJob, build_job_info, discover_jobs, pending_job
from .subscriptions import (
    Subscription,
    validate_subscription_auth,
)

Params = (
    AciQuery
    | QueryParams
    | Mapping[str, QueryValue]
    | Sequence[tuple[str, QueryValue]]
    | None
)
DEFAULT_TIMEOUT = 100.0
_DN_SAFE = "/[](),=:\"'"


class InsecureTLSWarning(UserWarning):
    pass


def _endpoint(value: str) -> str:
    value = value.strip()
    if "://" not in value:
        value = "https://" + (
            f"[{value}]"
            if value.count(":") > 1 and not value.startswith("[")
            else value
        )
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https":
        raise ConfigurationError("modern clients require an HTTPS endpoint")
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(
            "endpoint must contain only an HTTPS host and optional port"
        )
    path = parsed.path.rstrip("/")
    if path not in ("", "/api"):
        raise ConfigurationError("endpoint must not contain an application path")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"https://{host}{':' + str(parsed.port) if parsed.port else ''}"


def _proxy_profile(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if not parsed.hostname:
        raise ConfigurationError("proxy must include a host")
    host = parsed.hostname
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme or 'proxy'}://{host}{port}"


def _path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigurationError("request path must be a non-empty relative APIC path")
    if urlsplit(value).scheme or value.startswith("//") or "?" in value or "#" in value:
        raise ConfigurationError(
            "request path must not contain a host, query string, or fragment"
        )
    value = value.lstrip("/")
    if value == "api":
        return ""
    if value.startswith("api/"):
        value = value[4:]
    if any(part == ".." for part in value.split("/")):
        raise ConfigurationError("path traversal is not allowed")
    return value


class AciClient:
    """Thread-safe synchronous APIC client. Construction performs no I/O."""

    def __init__(
        self,
        endpoint: str,
        auth: Auth,
        nexus_dashboard_cluster_name: str | None = None,
        verify: bool | str | Path | ssl.SSLContext = True,
        connection_limits: HttpConnectionLimits = HttpConnectionLimits(),
        *,
        timeout: float = DEFAULT_TIMEOUT,
        proxy: str | None = None,
        transport: httpx.BaseTransport | None = None,
        runtime: SyncRuntime | None = None,
    ):
        self._endpoint = _endpoint(endpoint)
        self.auth = auth
        self._cluster = nexus_dashboard_cluster_name
        validate_auth(auth, bool(self._cluster))
        if verify is False:
            warnings.warn(
                "TLS certificate verification is disabled",
                InsecureTLSWarning,
                stacklevel=3,
            )
        principal = (
            auth.certificate_dn if isinstance(auth, CertificateAuth) else auth.username
        )
        domain = getattr(auth, "domain", None)
        self.connection_identity = ConnectionIdentity(
            endpoint=self._endpoint,
            principal=principal,
            authentication_type=type(auth).__name__,
            domain=domain,
            nexus_dashboard_cluster_name=self._cluster,
            tls_profile="insecure"
            if verify is False
            else str(verify)
            if verify is not True
            else "default",
            proxy_profile=_proxy_profile(proxy),
        )
        self._connection_info: ConnectionInfo | None = None
        self._closed = False
        self._runtime = runtime or SystemSyncRuntime()
        self._timeout = httpx.Timeout(
            timeout, pool=connection_limits.pool_timeout or timeout
        )
        if transport is not None and proxy is not None:
            raise ConfigurationError("proxy cannot be combined with a custom transport")
        limits = httpx.Limits(
            max_connections=connection_limits.max_connections,
            max_keepalive_connections=connection_limits.max_keepalive_connections,
            keepalive_expiry=connection_limits.keepalive_expiry,
        )
        self._http = httpx.Client(
            verify=ssl.create_default_context(cafile=str(verify))
            if isinstance(verify, Path)
            else verify,
            proxy=proxy,
            transport=transport,
            limits=limits,
            timeout=self._timeout,
        )

        self._session = AuthSession(
            auth,
            self._endpoint,
            self._cluster,
            principal,
            self._runtime,
            self._raw,
            self._http.cookies.clear,
        )

    @property
    def connection_info(self) -> ConnectionInfo | None:
        return self._session.connection_info or self._connection_info

    @property
    def is_authenticated(self) -> bool:
        return not self._closed and self._session.is_authenticated

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def session_token(self) -> str | None:
        """Return the current session token without I/O; treat it as a secret."""
        return self._session.token

    @property
    def session_info(self) -> SessionInfo:
        info = self._session.info
        return replace(info, authenticated=self.is_authenticated)

    def __copy__(self):
        raise TypeError(
            "clients cannot be copied; share this client or construct another"
        )

    def __deepcopy__(self, memo):
        raise TypeError(
            "clients cannot be deep-copied; share this client or construct another"
        )

    def _url(self, path: str) -> str:
        clean = _path(path)
        apic_path = "api/" + clean
        route = (
            f"api/v1/oneManage/apics/{quote(self._cluster or '', safe='')}/{apic_path}"
            if self._cluster
            else apic_path
        )
        return f"{self._endpoint}/{route}"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _raw(
        self,
        method: str,
        path: str | None = None,
        *,
        params: Params = None,
        data: Any = None,
        timeout: float | None = None,
        authenticate: bool = True,
        include_auth_headers: bool = True,
        url: str | None = None,
    ) -> httpx.Response:
        if self._closed:
            raise TransportError("client is closed")
        remaining_request_time()
        pairs = normalize_params(params)
        if url is None:
            if path is None:
                raise ConfigurationError("request path is required")
            url = self._url(path)
            if self._cluster and pairs:
                # OneManage forwards apicPath, not the proxy URL's query. Keep
                # the APIC query inside that path; preserve its encoded values
                # and literal separators while encoding its '?' delimiter.
                url += "%3F" + str(httpx.QueryParams(list(pairs)))
                pairs = ()
        body = (
            b""
            if data is None
            else json.dumps(data, ensure_ascii=False).encode("utf-8")
        )

        if authenticate:
            self.ensure_authenticated()
        headers = {"Accept": "application/json"}
        if body:
            headers["Content-Type"] = "application/json"
        request = self._http.build_request(
            method,
            url,
            params=list(pairs),
            content=body or None,
            headers=headers,
            timeout=bounded_timeout(
                self._timeout
                if timeout is None
                else httpx.Timeout(timeout, pool=self._timeout.pool)
            ),
        )
        if include_auth_headers:
            request.headers.update(
                self._session.headers(
                    request.method,
                    request.url.raw_path.decode("ascii"),
                    request.content,
                )
            )
        else:
            # An httpx cookie jar must not authenticate a public/login request.
            request.headers.pop("Cookie", None)
        try:
            response = self._http.send(request)
        except httpx.TimeoutException as exc:
            raise AciTimeoutError(str(exc)) from exc
        except httpx.TransportError as exc:
            raise TransportError(str(exc)) from exc
        if include_auth_headers and response.status_code == 401:
            self._session.invalidate(request)
        remaining_request_time()
        return response

    def login(self) -> SessionInfo:
        """Log in explicitly; ordinary requests authenticate lazily."""
        if self._closed:
            raise TransportError("client is closed")
        return self._session.authenticate(refresh=False)

    def ensure_authenticated(self) -> SessionInfo:
        """Log in or renew a session when needed, coordinating concurrent callers."""
        if self._closed:
            raise TransportError("client is closed")
        return self._session.authenticate(refresh=True)

    def logout(self) -> None:
        """End the server session and clear local credentials, even on failure."""
        self._session.logout()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Params = None,
        data: Any = None,
        timeout: float | None = None,
    ) -> AciResponse:
        return decode_response(
            self._raw(method, path, params=params, data=data, timeout=timeout),
            method,
            _path(path),
        )

    def get(
        self,
        path: str,
        *,
        params: Params = None,
        timeout: float | None = None,
    ) -> AciResponse:
        return self._request("GET", path, params=params, timeout=timeout)

    def post(
        self,
        path: str,
        *,
        data: Any = None,
        params: Params = None,
        timeout: float | None = None,
    ) -> AciResponse:
        return self._request("POST", path, data=data, params=params, timeout=timeout)

    def delete(
        self,
        path: str,
        *,
        params: Params = None,
        timeout: float | None = None,
    ) -> AciResponse:
        return self._request("DELETE", path, params=params, timeout=timeout)

    def get_class(
        self,
        class_name: str,
        *,
        params: Params = None,
        timeout: float | None = None,
    ) -> AciResponse:
        return self.get(
            f"class/{quote(class_name, safe='')}.json",
            params=params,
            timeout=timeout,
        )

    def get_mo(
        self, dn: str, *, params: Params = None, timeout: float | None = None
    ) -> AciResponse:
        return self.get(
            f"mo/{quote(dn.lstrip('/'), safe=_DN_SAFE)}.json",
            params=params,
            timeout=timeout,
        )

    def get_imdata(
        self, path: str, *, params: Params = None, timeout: float | None = None
    ) -> list[dict[str, Any]]:
        return [
            dict(x) for x in self.get(path, params=params, timeout=timeout).raw_imdata
        ]

    def post_mo(
        self,
        data: Any,
        *,
        dn: str | None = None,
        params: Params = None,
        timeout: float | None = None,
    ) -> AciResponse:
        if isinstance(data, ManagedObject):
            data = data.to_imdata()
        path = (
            "mo.json"
            if dn is None
            else f"mo/{quote(dn.lstrip('/'), safe=_DN_SAFE)}.json"
        )
        return self.post(path, data=data, params=params, timeout=timeout)

    def delete_mo(
        self, dn: str, *, params: Params = None, timeout: float | None = None
    ) -> AciResponse:
        return self.delete(
            f"mo/{quote(dn.lstrip('/'), safe=_DN_SAFE)}.json",
            params=params,
            timeout=timeout,
        )

    def iter_pages(
        self,
        path: str,
        *,
        params: Params = None,
        policy: PaginationPolicy = PaginationPolicy(),
        timeout: float | None = None,
    ) -> Iterator[AciResponse]:
        """Yield pages until exhausted or a policy limit is reached.

        A deadline includes authentication and requests; pages arriving after it
        are discarded. HTTP phase timeouts are capped at the remaining budget.
        """
        base = normalize_params(params)
        page_size = min(policy.page_size, 2000)
        deadline = (
            Deadline(self._runtime, policy.deadline)
            if policy.deadline is not None
            else None
        )
        count = 0
        page = 0
        while policy.max_pages is None or page < policy.max_pages:
            if deadline is not None and deadline.remaining <= 0:
                break
            try:
                with deadline.activate() if deadline is not None else nullcontext():
                    response = self.get(
                        path,
                        params=base.replace(page=page, page_size=page_size),
                        timeout=timeout,
                    )
            except AciTimeoutError:
                if deadline is not None and deadline.remaining <= 0:
                    break
                raise
            if response.is_empty:
                break
            if (
                policy.max_items is not None
                and count + len(response.objects) > policy.max_items
            ):
                n = policy.max_items - count
                response = AciResponse(
                    response.objects[:n],
                    response.raw_imdata[:n],
                    response.total_count,
                    response.status_code,
                    response.headers,
                    response.raw,
                )
            yield response
            count += len(response.objects)
            page += 1
            if len(response.objects) < page_size or (
                policy.max_items is not None and count >= policy.max_items
            ):
                break

    def iter_objects(
        self,
        path: str,
        *,
        params: Params = None,
        policy: PaginationPolicy = PaginationPolicy(),
        timeout: float | None = None,
    ) -> Iterator[ManagedObject]:
        for page in self.iter_pages(
            path, params=params, policy=policy, timeout=timeout
        ):
            yield from page.objects

    def collect_pages(
        self,
        path: str,
        *,
        params: Params = None,
        policy: PaginationPolicy = PaginationPolicy(),
        timeout: float | None = None,
    ) -> list[AciResponse]:
        return list(
            self.iter_pages(path, params=params, policy=policy, timeout=timeout)
        )

    def collect_objects(
        self,
        path: str,
        *,
        params: Params = None,
        policy: PaginationPolicy = PaginationPolicy(),
        timeout: float | None = None,
    ) -> list[ManagedObject]:
        return list(
            self.iter_objects(path, params=params, policy=policy, timeout=timeout)
        )

    def list_login_domains(self) -> LoginDomains:
        path = (
            "/api/v1/infra/logindomains"
            if self._cluster
            else "/api/aaaListDomains.json"
        )
        response = checked_json(
            self._raw(
                "GET",
                authenticate=False,
                include_auth_headers=False,
                url=self._endpoint + path,
            ),
            "GET",
            path,
        )
        return decode_domains(response, bool(self._cluster))

    def discover_version(self) -> ConnectionInfo:
        if self.connection_info:
            return self.connection_info
        response = self.get_class("firmwareCtrlrRunning")
        versions = {
            str(obj.attributes["version"])
            for obj in response.objects
            if obj.attributes.get("version")
        }
        if len(versions) != 1:
            raise ResponseDecodeError(
                "APIC controller firmware version is missing or ambiguous: "
                + ", ".join(sorted(versions))
            )
        raw = versions.pop()
        self._connection_info = ConnectionInfo(
            parse_version(raw), raw, bool(self._cluster), self._cluster
        )
        return self._connection_info

    def subscribe(
        self,
        path: str,
        *,
        params: Params = None,
        refresh_timeout: int | None = None,
        timeout: float | None = None,
    ) -> Subscription:
        validate_subscription_auth(self.auth, self._cluster)
        pairs = normalize_params(params).replace(subscription="yes")
        if refresh_timeout is not None:
            pairs = pairs.replace(refresh_timeout=refresh_timeout)
        else:
            refresh_timeout = next(
                (int(value) for key, value in pairs if key == "refresh-timeout"),
                None,
            )
        response = self.get(path, params=pairs, timeout=timeout)
        sid = decode_subscription_id(response.raw)
        initial = SubscriptionInitialResponse(
            sid,
            response.objects,
            response.raw_imdata,
            response.total_count,
            response.raw,
        )
        return Subscription(self, initial, refresh_timeout)

    def subscribe_class(
        self,
        class_name: str,
        *,
        params: Params = None,
        refresh_timeout: int | None = None,
        timeout: float | None = None,
    ) -> Subscription:
        return self.subscribe(
            f"class/{quote(class_name, safe='')}.json",
            params=params,
            refresh_timeout=refresh_timeout,
            timeout=timeout,
        )

    def subscribe_mo(
        self,
        dn: str,
        *,
        params: Params = None,
        refresh_timeout: int | None = None,
        timeout: float | None = None,
    ) -> Subscription:
        return self.subscribe(
            f"mo/{quote(dn.lstrip('/'), safe=_DN_SAFE)}.json",
            params=params,
            refresh_timeout=refresh_timeout,
            timeout=timeout,
        )

    def create_snapshot(
        self,
        description: str = "snapshot",
        target_dn: str | None = None,
        name: str | None = None,
        include_secure_fields: bool = False,
    ) -> SnapshotJob:
        name = name or f"aciclient-{uuid4().hex[:16]}"
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", name):
            raise ConfigurationError(
                "snapshot policy name must be 1–64 APIC name characters"
            )
        now = datetime.now(timezone.utc)
        policy_dn = f"uni/fabric/configexp-{name}"
        baseline = frozenset(obj.dn for obj in discover_jobs(self, policy_dn))
        attributes = {
            "adminSt": "triggered",
            "descr": description,
            "dn": policy_dn,
            "format": "json",
            "includeSecureFields": "yes" if include_secure_fields else "no",
            "name": name,
            "snapshot": "yes",
            "targetDn": target_dn or "",
        }
        data = {
            "configExportP": {
                "attributes": attributes,
            }
        }
        response = self.post_mo(data)
        info = SnapshotJobInfo(
            policy_dn=policy_dn,
            job_dn=None,
            name=name,
            description=description,
            target_dn=target_dn,
            submitted_at=now,
            state=JobState.PENDING,
            attributes=attributes,
            raw=response.raw,
        )
        return SnapshotJob(self, info, self._runtime, baseline=baseline)

    def get_snapshot_job(self, job_dn_or_name: str) -> SnapshotJob:
        if "/" in job_dn_or_name:
            response = self.get_mo(job_dn_or_name)
            info = build_job_info(response.one(), response.raw)
        else:
            policy_dn = f"uni/fabric/configexp-{job_dn_or_name}"
            info = pending_job(policy_dn, job_dn_or_name)
        job = SnapshotJob(self, info, self._runtime)
        if info.job_dn is None:
            job.refresh()
        return job

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._http.close()
