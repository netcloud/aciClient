"""Password and certificate authentication, session renewal, and discovery."""

import base64
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from aciClient import (
    AciApiError,
    AciClient,
    AciQuery,
    AuthenticationError,
    AuthorizationError,
    CertificateAuth,
    PaginationPolicy,
    PasswordAuth,
    ResponseDecodeError,
    eq,
)
from aciClient.exceptions import TransportError
from aciClient.testing import (
    ExpectedRequest,
    ManualClock,
    ScriptedTransport,
    imdata_response,
    login_response,
    managed_object,
)

from .helpers import client_for, jwt, login_expectation


def test_direct_login_query_and_managed_object():
    transport = ScriptedTransport(
        [
            login_expectation(domain="LDAP"),
            ExpectedRequest(
                "GET",
                "/api/class/fvTenant.json",
                query=[("query-target-filter", 'eq(fvTenant.name,"a\\"b")')],
                response=imdata_response(
                    managed_object("fvTenant", {"dn": "uni/tn-a", "name": "a"})
                ),
            ),
        ]
    )
    with AciClient(
        "apic.example", PasswordAuth("admin", "secret", "LDAP"), transport=transport
    ) as client:
        response = client.get_class(
            "fvTenant", params=AciQuery(query_target_filter=eq("fvTenant.name", 'a"b'))
        )
        assert response.one().dn == "uni/tn-a"
        assert response.total_count == 1
        assert client.connection_info.apic_version.major == 6
    assert transport.history[0].json["aaaUser"]["attributes"]["pwd"] == "********"
    transport.assert_all_consumed()


def test_many_threads_share_one_login_session():
    lock = threading.Lock()
    counts = {"login": 0, "get": 0}

    def handler(request):
        with lock:
            if request.url.path.endswith("aaaLogin.json"):
                counts["login"] += 1
                return httpx.Response(200, json=login_response(), request=request)
            counts["get"] += 1
            return httpx.Response(200, json=imdata_response(), request=request)

    client = AciClient(
        "apic.example",
        PasswordAuth("admin", "secret"),
        transport=httpx.MockTransport(handler),
    )
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: client.get_class("fvTenant"), range(24)))
    assert counts == {"login": 1, "get": 24}


def test_certificate_signature_covers_exact_path_query_and_body():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signature = base64.b64encode(
        key.sign(
            b"GET/api/class/fvTenant.json?page=0",
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    ).decode()
    cookie = "; ".join(
        [
            "APIC-Certificate-Fingerprint=cert-name",
            "APIC-Certificate-Algorithm=v1.0",
            "APIC-Certificate-DN=uni/userext/user-cert",
            f"APIC-Request-Signature={signature}",
        ]
    )
    transport = ScriptedTransport(
        [
            ExpectedRequest(
                "GET",
                "/api/class/fvTenant.json",
                query=[("page", "0")],
                headers={"Cookie": cookie},
                response=imdata_response(),
            )
        ]
    )
    client = AciClient(
        "apic.example",
        CertificateAuth("cert-name", key, "uni/userext/user-cert"),
        transport=transport,
    )
    client.get_class("fvTenant", params={"page": 0})


def test_login_domain_discovery_before_login():
    response = imdata_response(
        managed_object(
            "aaaDomain", {"name": "LDAP", "realm": "ldap", "isDefault": "yes"}
        )
    )
    transport = ScriptedTransport(
        [ExpectedRequest("GET", "/api/aaaListDomains.json", response=response)]
    )
    client = AciClient(
        "apic.example", PasswordAuth("admin", "secret"), transport=transport
    )
    domains = client.list_login_domains()
    assert domains.default_domain == "LDAP"
    assert not client.is_authenticated


def test_domain_discovery_uses_client_transport_errors():
    transport = ScriptedTransport(
        [
            ExpectedRequest(
                "GET",
                "/api/aaaListDomains.json",
                response=httpx.ConnectError("down"),
            )
        ]
    )
    client = AciClient(
        "apic.example", PasswordAuth("admin", "secret"), transport=transport
    )
    with pytest.raises(TransportError):
        client.list_login_domains()


def test_session_token_tracks_authentication_lifecycle_without_io():
    transport = ScriptedTransport(
        [
            login_expectation(),
            ExpectedRequest("POST", "/api/aaaLogout.json", response=imdata_response()),
        ]
    )
    with AciClient(
        "apic.example", PasswordAuth("admin", "secret"), transport=transport
    ) as client:
        assert client.session_token is None
        assert transport.history == []

        client.login()
        assert client.session_token == "test-token"
        assert len(transport.history) == 1

        client.logout()
        assert client.session_token is None
        assert len(transport.history) == 2

    transport.assert_all_consumed()


def test_logout_clears_session_metadata():
    transport = ScriptedTransport(
        [
            login_expectation(),
            ExpectedRequest("POST", "/api/aaaLogout.json", response=imdata_response()),
        ]
    )
    client = AciClient(
        "apic.example", PasswordAuth("admin", "secret"), transport=transport
    )
    client.login()
    assert client.session_info.authenticated_at is not None

    client.logout()

    assert not client.session_info.authenticated
    assert client.session_info.authenticated_at is None
    assert client.session_info.refresh_deadline is None


def test_flat_and_wrapped_domains_preserved():
    records = [
        {"name": "TACACS", "type": "OTHER"},
        {"aaaDomain": {"attributes": {"name": "local", "isDefault": "yes"}}},
    ]
    with client_for(lambda r: httpx.Response(200, json={"imdata": records})) as client:
        domains = client.list_login_domains()
        assert [d.name for d in domains.domains] == ["TACACS", "local"]
        assert domains.default_domain == "local"
        assert domains.domains[0].raw["type"] == "OTHER"
        assert not client.is_authenticated


def test_nd_default_domain_does_not_treat_string_no_as_true():
    body = {
        "defaultDomain": "local",
        "domains": [
            {"name": "local", "realm": "Local"},
            {"name": "ldap", "isDefault": "no"},
        ],
    }
    with client_for(lambda r: httpx.Response(200, json=body), gateway=True) as client:
        domains = client.list_login_domains()
        assert domains.default_domain == "local"
        assert [d.is_default for d in domains.domains] == [True, False]


def test_concurrent_refresh_rotates_once_with_get():
    clock = ManualClock()
    calls = []
    lock = threading.Lock()

    def handler(request):
        with lock:
            calls.append((request.method, request.url.path))
            if request.url.path.endswith("aaaLogin.json"):
                return httpx.Response(200, json=login_response())
            assert request.method == "GET" and not request.content
            return httpx.Response(200, json=login_response(token="rotated"))

    with client_for(handler, clock=clock) as client:
        client.login()
        clock.advance(580)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: client.ensure_authenticated(), range(16)))
        assert client.session_token == "rotated"
        assert calls == [
            ("POST", "/api/aaaLogin.json"),
            ("GET", "/api/aaaRefresh.json"),
        ]


@pytest.mark.parametrize("maximum", [None, 100])
def test_expired_or_maximum_session_reauthenticates_before_sending(maximum):
    clock = ManualClock()
    logins = []

    def handler(request):
        if request.url.path.endswith("aaaLogin.json"):
            logins.append(request)
            body = login_response(
                token="token-" + str(len(logins)), refresh_timeout=600
            )
            if maximum is not None:
                body["imdata"][0]["aaaLogin"]["attributes"][
                    "maximumLifetimeSeconds"
                ] = str(maximum)
            return httpx.Response(200, json=body)
        assert request.headers["Cookie"] == "APIC-cookie=token-2"
        return httpx.Response(200, json=imdata_response())

    with client_for(handler, clock=clock) as client:
        client.login()
        clock.advance(120 if maximum else 601)
        assert not client.is_authenticated
        client.get_class("fvTenant")
        assert len(logins) == 2


def test_failed_request_invalidates_without_replay_then_explicit_login_works():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("aaaLogin.json"):
            return httpx.Response(200, json=login_response())
        return httpx.Response(401, json={"message": "expired", "code": 401})

    with client_for(handler) as client:
        with pytest.raises(AuthenticationError):
            client.post_mo({"fvTenant": {"attributes": {"dn": "uni/tn-test"}}})
        assert client.session_token is None
        assert len(calls) == 2
        client.login()
        assert len(calls) == 3


def test_refresh_rejection_clears_session_and_propagates():
    clock = ManualClock()

    def handler(request):
        if request.url.path.endswith("aaaLogin.json"):
            return httpx.Response(200, json=login_response())
        return httpx.Response(403, json={"message": "refresh rejected", "code": 403})

    with client_for(handler, clock=clock) as client:
        client.login()
        clock.advance(580)
        with pytest.raises(AuthorizationError):
            client.ensure_authenticated()
        assert (
            client.session_token is None
            and client.session_info.refresh_deadline is None
        )


def test_nd_jwt_refresh_uses_infra_endpoint():
    token1 = jwt(datetime.now(timezone.utc).timestamp() + 300)
    token2 = jwt(datetime.now(timezone.utc).timestamp() + 900)
    calls = []
    clock = ManualClock()

    def handler(request):
        calls.append(request.url.path)
        assert request.method == "POST"
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"jwttoken": token1})
        assert request.url.path == "/api/v1/infra/refresh"
        assert request.headers["Cookie"] == "AuthCookie=" + token1
        return httpx.Response(200, json={"jwttoken": token2})

    with client_for(handler, clock=clock, gateway=True) as client:
        client.login()
        clock.advance(280)
        client.ensure_authenticated()
        assert client.session_token == token2
        assert calls == ["/api/v1/infra/login", "/api/v1/infra/refresh"]


def test_logout_failure_is_reported_and_local_state_cleared():
    def handler(request):
        return (
            httpx.Response(200, json=login_response())
            if request.url.path.endswith("aaaLogin.json")
            else httpx.Response(
                500, json={"message": "logout unavailable", "code": 500}
            )
        )

    with client_for(handler) as client:
        client.login()
        with pytest.raises(AciApiError, match="logout unavailable"):
            client.logout()
        assert client.session_token is None and not client.is_authenticated


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_signature_matches_actual_request_bytes_with_quotes_and_unicode(method):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def handler(request):
        cookies = dict(x.split("=", 1) for x in request.headers["Cookie"].split("; "))
        key.public_key().verify(
            base64.b64decode(cookies["APIC-Request-Signature"]),
            request.method.encode() + request.url.raw_path + request.content,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        assert b"%22" in request.url.raw_path
        return httpx.Response(200, json=imdata_response())

    auth = CertificateAuth("cert", key, "uni/userext/user-test/usercert-cert")
    with AciClient(
        "apic.example", auth, transport=httpx.MockTransport(handler)
    ) as client:
        dn = 'uni/tn-tést/annotationKey-["quoted"]'
        if method == "GET":
            client.get_mo(dn, params={"query-target-filter": 'eq(fvTenant.name,"a b")'})
        elif method == "DELETE":
            client.delete_mo(dn)
        else:
            client.post_mo({"fvTenant": {"attributes": {"dn": dn}}}, dn=dn)


@pytest.mark.parametrize("versions", [("6.2(2e)", "6.2(2e)"), ("6.0(9f)", "6.2(2e)")])
def test_firmware_fallback_refuses_mixed_versions(versions):
    def handler(request):
        assert request.url.path == "/api/class/firmwareCtrlrRunning.json"
        assert not request.url.query
        return httpx.Response(
            200,
            json=imdata_response(
                *[
                    managed_object("firmwareCtrlrRunning", {"version": v})
                    for v in versions
                ]
            ),
        )

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with AciClient(
        "apic.example",
        CertificateAuth("cert", key, "user-cert"),
        transport=httpx.MockTransport(handler),
    ) as client:
        if len(set(versions)) == 1:
            assert client.discover_version().apic_version_raw == versions[0]
        else:
            with pytest.raises(ResponseDecodeError, match="ambiguous"):
                client.discover_version()


@pytest.mark.parametrize("authenticated", [False, True])
def test_nd_public_domains_never_authenticate_or_send_cached_cookie(authenticated):
    calls = []
    domain_body = {
        "defaultDomain": "local",
        "domains": [
            {
                "name": "local",
                "realm": "Local",
                "passwordSettings": {"minimumLength": 8, "strongCheckEnabled": True},
            }
        ],
    }

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"jwttoken": "nd-token"})
        assert request.url.path == "/api/v1/infra/logindomains"
        assert (
            "Cookie" not in request.headers and "Authorization" not in request.headers
        )
        return httpx.Response(200, json=domain_body)

    with client_for(handler, gateway=True) as client:
        if authenticated:
            client.login()
            client._http.cookies.set("AuthCookie", "jar-token")
        domains = client.list_login_domains()
        assert domains.default_domain == "local"
        assert (
            domains.domains[0].raw["passwordSettings"]
            == domain_body["domains"][0]["passwordSettings"]
        )
        assert client.is_authenticated == authenticated
    assert calls == (["/api/v1/infra/login"] if authenticated else []) + [
        "/api/v1/infra/logindomains"
    ]


def test_nd_public_domain_rejection_does_not_trigger_login_fallback():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(401, json={"error": "Authorization field missing"})

    with client_for(handler, gateway=True) as client:
        with pytest.raises(AuthenticationError, match="Authorization field missing"):
            client.list_login_domains()
        assert not client.is_authenticated
    assert calls == ["/api/v1/infra/logindomains"]


@pytest.mark.parametrize("lifetime", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_session_lifetime_rejects_login_and_clears_state(lifetime):
    response = login_response(refresh_timeout=lifetime)
    with client_for(lambda request: httpx.Response(200, json=response)) as client:
        with pytest.raises(ResponseDecodeError, match="authentication lifetime"):
            client.login()
        assert not client.is_authenticated and client.session_token is None


@pytest.mark.parametrize("expiry", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_jwt_expiry_falls_back_to_returned_lifetime(expiry):
    calls = []
    clock = ManualClock()

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "jwttoken": jwt(expiry),
                "refreshTimeoutSeconds": 300,
            },
        )

    with client_for(handler, gateway=True, clock=clock) as client:
        client.login()
        clock.advance(200)
        client.ensure_authenticated()
        assert calls == ["/api/v1/infra/login"]
        clock.advance(90)
        client.ensure_authenticated()
        assert calls == ["/api/v1/infra/login", "/api/v1/infra/refresh"]


def test_new_login_version_replaces_a_previously_discovered_version():
    logins = []

    def handler(request):
        if request.url.path.endswith("aaaLogin.json"):
            logins.append(request)
            body = login_response(version="6.1(1a)")
            if len(logins) == 1:
                body["imdata"][0]["aaaLogin"]["attributes"].pop("version")
            return httpx.Response(200, json=body)
        if request.url.path.endswith("firmwareCtrlrRunning.json"):
            return httpx.Response(
                200,
                json=imdata_response(
                    managed_object("firmwareCtrlrRunning", {"version": "6.0(2h)"})
                ),
            )
        return httpx.Response(200, json=imdata_response())

    with AciClient(
        "apic.example", PasswordAuth("u", "p"), transport=httpx.MockTransport(handler)
    ) as client:
        client.login()
        assert client.discover_version().apic_version_raw == "6.0(2h)"
        client.logout()
        client.login()
        assert client.connection_info.apic_version_raw == "6.1(1a)"


def test_authentication_wait_respects_budget_without_cancelling_other_callers():
    started, release = Event(), Event()
    logins = []

    def handler(request):
        if request.url.path.endswith("aaaLogin.json"):
            logins.append(request)
            started.set()
            assert release.wait(5)
            return httpx.Response(200, json=login_response())
        return httpx.Response(200, json=imdata_response())

    with AciClient(
        "apic.example", PasswordAuth("u", "p"), transport=httpx.MockTransport(handler)
    ) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            login = pool.submit(client.login)
            try:
                assert started.wait(5)
                pages = pool.submit(
                    client.collect_objects,
                    "class/fvTenant.json",
                    policy=PaginationPolicy(deadline=0.05),
                )
                assert pages.result(timeout=2) == []
                assert not login.done()
            finally:
                release.set()
            assert login.result(timeout=2).authenticated
        client.get_class("fvTenant")
    assert len(logins) == 1
