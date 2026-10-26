"""Authentication sessions, token renewal, and certificate request signing."""

from __future__ import annotations

import base64
import json
import math
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from ._responses import checked_json, decode_login, parse_version
from ._timeouts import remaining_request_time
from .auth import Auth, CertificateAuth, NexusDashboardPasswordAuth, PasswordAuth
from .exceptions import AuthenticationError, ConfigurationError, ResponseDecodeError
from .models import ConnectionInfo, SessionInfo
from .runtime import SyncRuntime


def _qualified_user(auth: PasswordAuth) -> str:
    already = auth.username.startswith("apic:") and "\\" in auth.username
    if auth.domain and already:
        raise ConfigurationError(
            "username is already APIC-qualified; do not also supply domain"
        )
    return f"apic:{auth.domain}\\{auth.username}" if auth.domain else auth.username


def _jwt_lifetime(token: str, now: datetime) -> float | None:
    """Read expiry as a refresh hint; authentication is verified by ND."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
        if not isinstance(claims, Mapping):
            return None
        expiry = float(claims["exp"])
        if math.isfinite(expiry):
            return max(0.0, expiry - now.timestamp())
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError):
        pass
    return None


def validate_auth(auth: Auth, gateway: bool) -> None:
    if gateway and not isinstance(auth, NexusDashboardPasswordAuth):
        raise ConfigurationError("gateway mode requires NexusDashboardPasswordAuth")
    if not gateway and not isinstance(auth, (PasswordAuth, CertificateAuth)):
        raise ConfigurationError(
            "direct APIC mode requires PasswordAuth or CertificateAuth"
        )


class AuthSession:
    """Coordinate authentication independently of HTTP routing and public methods.

    The client supplies request sending and cookie cleanup. This object owns
    session state and serializes login, refresh, and logout across threads.
    """

    def __init__(
        self,
        auth: Auth,
        endpoint: str,
        cluster: str | None,
        principal: str,
        runtime: SyncRuntime,
        send: Callable[..., httpx.Response],
        clear_cookies: Callable[[], None],
    ) -> None:
        self.auth = auth
        self._endpoint = endpoint
        self._cluster = cluster
        self._principal = principal
        self._runtime = runtime
        self._send = send
        self._clear_cookies = clear_cookies
        self.connection_info: ConnectionInfo | None = None
        self._token: str | None = None
        self._authenticated_at: datetime | None = None
        self._last_refresh_at: datetime | None = None
        self._refresh_deadline: datetime | None = None
        self._refresh_at_monotonic: float | None = None
        self._maximum_deadline: datetime | None = None
        self._maximum_at_monotonic: float | None = None
        self._auth_condition = threading.Condition(threading.RLock())
        self._auth_in_progress = False
        self._auth_error: BaseException | None = None

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def is_authenticated(self) -> bool:
        return isinstance(self.auth, CertificateAuth) or (
            self._token is not None and not self._session_expired()
        )

    @property
    def info(self) -> SessionInfo:
        return SessionInfo(
            self.is_authenticated,
            self._principal,
            self._authenticated_at,
            self._refresh_deadline,
            self._maximum_deadline,
            self._last_refresh_at,
        )

    def invalidate(self, request: httpx.Request) -> None:
        """Clear a rejected token only if this request used the current session."""
        with self._auth_condition:
            cookie = request.headers.get("Cookie", "")
            if self._token and cookie.endswith("=" + self._token):
                self._clear_session()

    def headers(self, method: str, wire_path: str, body: bytes) -> dict[str, str]:
        headers = {}
        if isinstance(self.auth, CertificateAuth):
            try:
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.primitives.asymmetric import padding

                key = self.auth.private_key
                if isinstance(key, Path):
                    key = key.read_bytes()
                if isinstance(key, bytes):
                    password = self.auth.private_key_password
                    if isinstance(password, str):
                        password = password.encode()
                    key = serialization.load_pem_private_key(key, password=password)
                signature = key.sign(
                    method.upper().encode() + wire_path.encode() + body,
                    padding.PKCS1v15(),
                    hashes.SHA256(),
                )
            except Exception as exc:
                raise ConfigurationError(
                    "unable to load or use certificate private key"
                ) from exc
            cookie = {
                "APIC-Certificate-Fingerprint": self.auth.certificate_name,
                "APIC-Certificate-Algorithm": "v1.0",
                "APIC-Certificate-DN": self.auth.certificate_dn,
                "APIC-Request-Signature": base64.b64encode(signature).decode(),
            }
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookie.items())
        elif self._token:
            cookie_name = "AuthCookie" if self._cluster else "APIC-cookie"
            headers["Cookie"] = f"{cookie_name}={self._token}"
        return headers

    def _set_login(self, raw: Mapping[str, Any]) -> None:
        values, token, version = decode_login(raw, bool(self._cluster))
        if not token:
            raise AuthenticationError("authentication response did not contain a token")
        now = datetime.now(timezone.utc)
        self._token = str(token)
        self._authenticated_at = self._authenticated_at or now
        self._last_refresh_at = now
        refresh = values.get("refreshTimeoutSeconds")
        maximum = values.get("maximumLifetimeSeconds")
        if self._cluster:
            jwt_lifetime = _jwt_lifetime(self._token, now)
            if jwt_lifetime is not None:
                refresh = jwt_lifetime
        self._refresh_deadline = None
        self._refresh_at_monotonic = None
        if refresh is not None:
            duration = self._session_seconds(refresh)
            self._refresh_deadline = now + timedelta(seconds=duration)
            self._refresh_at_monotonic = self._runtime.monotonic() + duration
        if maximum is not None and self._maximum_deadline is None:
            duration = self._session_seconds(maximum)
            self._maximum_deadline = self._authenticated_at + timedelta(
                seconds=duration
            )
            self._maximum_at_monotonic = self._runtime.monotonic() + duration
        if version:
            self.connection_info = ConnectionInfo(
                parse_version(str(version)),
                str(version),
                bool(self._cluster),
                self._cluster,
            )

    @staticmethod
    def _session_seconds(value: Any) -> float:
        try:
            seconds = float(value)
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError
            return seconds
        except (ValueError, TypeError) as exc:
            raise ResponseDecodeError("invalid authentication lifetime") from exc

    def _session_expired(self) -> bool:
        now = self._runtime.monotonic()
        return any(
            deadline is not None and now >= deadline
            for deadline in (
                self._refresh_at_monotonic,
                self._maximum_at_monotonic,
            )
        )

    def _clear_session(self) -> None:
        self._token = None
        self._authenticated_at = None
        self._last_refresh_at = None
        self._refresh_deadline = None
        self._refresh_at_monotonic = None
        self._maximum_deadline = None
        self._maximum_at_monotonic = None
        self._clear_cookies()

    def authenticate(self, *, refresh: bool) -> SessionInfo:
        if isinstance(self.auth, CertificateAuth):
            return self.info
        with self._auth_condition:
            if self._auth_in_progress:
                while self._auth_in_progress:
                    self._auth_condition.wait(timeout=remaining_request_time())
                    remaining_request_time()
                if self._auth_error is not None:
                    raise self._auth_error
            if self._token and self._session_expired():
                self._clear_session()
            renew = (
                bool(self._token)
                and refresh
                and self._refresh_at_monotonic is not None
                and self._runtime.monotonic() >= self._refresh_at_monotonic - 30
            )
            if self._token and not renew:
                return self.info
            self._auth_in_progress = True
            self._auth_error = None
        try:
            if renew:
                response = (
                    self._send(
                        "POST",
                        authenticate=False,
                        url=self._endpoint + "/api/v1/infra/refresh",
                    )
                    if self._cluster
                    else self._send("GET", "aaaRefresh.json", authenticate=False)
                )
            elif isinstance(self.auth, PasswordAuth):
                data = {
                    "aaaUser": {
                        "attributes": {
                            "name": _qualified_user(self.auth),
                            "pwd": self.auth.password,
                        }
                    }
                }
                response = self._send(
                    "POST",
                    "aaaLogin.json",
                    data=data,
                    authenticate=False,
                    include_auth_headers=False,
                )
            else:
                data = {
                    "domain": self.auth.domain,
                    "userName": self.auth.username,
                    "userPasswd": self.auth.password,
                }
                response = self._send(
                    "POST",
                    data=data,
                    authenticate=False,
                    include_auth_headers=False,
                    url=self._endpoint + "/api/v1/infra/login",
                )
            parsed = checked_json(
                response, response.request.method, response.request.url.path
            )
            self._set_login(parsed)
            result = self.info
        except Exception as exc:
            with self._auth_condition:
                self._clear_session()
                self._auth_error = exc
            raise
        finally:
            with self._auth_condition:
                self._auth_in_progress = False
                self._auth_condition.notify_all()
        return result

    def logout(self) -> None:
        with self._auth_condition:
            while self._auth_in_progress:
                self._auth_condition.wait()
            if self._token:
                try:
                    response = (
                        self._send(
                            "POST",
                            authenticate=False,
                            url=self._endpoint + "/api/v1/infra/logout",
                        )
                        if self._cluster
                        else self._send(
                            "POST",
                            "aaaLogout.json",
                            data={
                                "aaaUser": {
                                    "attributes": {"name": _qualified_user(self.auth)}
                                }
                            },
                            authenticate=False,
                        )
                    )
                    checked_json(response, "POST", response.request.url.path)
                finally:
                    self._clear_session()
            else:
                self._clear_session()
