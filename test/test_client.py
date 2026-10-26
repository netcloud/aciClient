"""Client configuration, lifecycle, request paths, and public value objects."""

import copy
import ssl
from pathlib import Path

import certifi
import httpx
import pytest

from aciClient import (
    AciClient,
    CertificateAuth,
    HttpConnectionLimits,
    ManagedObject,
    NexusDashboardPasswordAuth,
    PasswordAuth,
)
from aciClient.exceptions import ConfigurationError, QueryParameterError
from aciClient.query import normalize_params
from aciClient.testing import (
    ExpectedRequest,
    ScriptedTransport,
    imdata_response,
    login_response,
)

from .helpers import login_expectation


@pytest.mark.parametrize(
    "auth,cluster",
    [
        (NexusDashboardPasswordAuth("id", "secret"), None),
        (PasswordAuth("u", "p"), "cluster"),
    ],
)
def test_invalid_auth_mode_rejected_without_io(auth, cluster):
    with pytest.raises(ConfigurationError):
        AciClient(
            "apic.example",
            auth,
            nexus_dashboard_cluster_name=cluster,
            transport=ScriptedTransport(),
        )


def test_dn_is_preserved_and_safely_encoded():
    dn = (
        "uni/tn-a/ap-b/epg-e/rsPathAtt-[topology/pod-1/paths-101/pathep-[eth1/1 space]]"
    )
    path = "/api/mo/uni/tn-a/ap-b/epg-e/rsPathAtt-[topology/pod-1/paths-101/pathep-[eth1/1 space]].json"
    transport = ScriptedTransport(
        [login_expectation(), ExpectedRequest("GET", path, response=imdata_response())]
    )
    AciClient(
        "apic.example", PasswordAuth("admin", "secret"), transport=transport
    ).get_mo(dn)


def test_query_validation_and_models():
    with pytest.raises(QueryParameterError):
        normalize_params([("page", 0), ("page", 1)])
    child = ManagedObject.create("fvAEPg", {"dn": "uni/tn-a/ap-a/epg-a"})
    tenant = ManagedObject.create("fvTenant", {"dn": "uni/tn-a"}, (child,))
    assert ManagedObject.from_imdata(tenant.to_imdata()) == tenant
    assert tenant.require_child("fvAEPg") == child


def test_copy_is_rejected():
    client = AciClient(
        "apic.example", PasswordAuth("u", "p"), transport=ScriptedTransport()
    )
    with pytest.raises(TypeError):
        copy.copy(client)
    with pytest.raises(TypeError):
        copy.deepcopy(client)


def test_public_arguments_reject_typos():
    with pytest.raises(TypeError, match="unexpected keyword argument 'timout'"):
        AciClient("apic.example", PasswordAuth("u", "p"), timout=1)

    client = AciClient(
        "apic.example", PasswordAuth("u", "p"), transport=ScriptedTransport()
    )
    with pytest.raises(TypeError, match="unexpected keyword argument 'timout'"):
        client.get_class("fvTenant", timout=1)


def test_client_closes_injected_transport():
    transport = ScriptedTransport()
    AciClient(
        "nd.example",
        NexusDashboardPasswordAuth("id", "secret"),
        nexus_dashboard_cluster_name="c",
        transport=transport,
    ).close()
    assert transport.closed


def test_certificate_identity_distinguishes_users_with_the_same_certificate_name():
    def identity(user):
        auth = CertificateAuth(
            "automation", b"unused", f"uni/userext/user-{user}/usercert-automation"
        )
        with AciClient("apic.example", auth, transport=ScriptedTransport()) as client:
            return client.connection_identity

    assert identity("ops") != identity("admin")
    assert identity("ops") == identity("ops")
    assert identity("ops").principal == "uni/userext/user-ops/usercert-automation"


def test_ca_path_creates_a_verified_ssl_context(monkeypatch):
    http_client = httpx.Client
    contexts = []

    def capture_client(**kwargs):
        contexts.append(kwargs["verify"])
        return http_client(**kwargs)

    monkeypatch.setattr(httpx, "Client", capture_client)
    transport = ScriptedTransport(
        [ExpectedRequest("GET", "/api/aaaListDomains.json", response={"imdata": []})]
    )
    bundle = Path(certifi.where())
    with AciClient(
        "apic.example", PasswordAuth("u", "p"), verify=bundle, transport=transport
    ) as client:
        assert client.list_login_domains().domains == ()
        assert client.connection_identity.tls_profile == str(bundle)

    context = contexts[0]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.get_ca_certs()
    transport.assert_all_consumed()


@pytest.mark.parametrize("pool_timeout", [None, 0.2])
@pytest.mark.parametrize("request_timeout", [None, 5.0, 0.0])
def test_login_and_requests_preserve_pool_timeout(pool_timeout, request_timeout):
    requests = []

    def handler(request):
        requests.append(request)
        payload = (
            login_response()
            if request.url.path.endswith("aaaLogin.json")
            else imdata_response()
        )
        return httpx.Response(200, json=payload)

    with AciClient(
        "apic.example",
        PasswordAuth("u", "p"),
        connection_limits=HttpConnectionLimits(pool_timeout=pool_timeout),
        transport=httpx.MockTransport(handler),
    ) as client:
        client.get_class("fvTenant", timeout=request_timeout)

    assert len(requests) == 2
    login_timeout, read_timeout = [r.extensions["timeout"] for r in requests]
    expected_pool = 100.0 if pool_timeout is None else pool_timeout
    assert login_timeout == {
        "connect": 100.0,
        "read": 100.0,
        "write": 100.0,
        "pool": expected_pool,
    }
    expected_timeout = 100.0 if request_timeout is None else request_timeout
    assert read_timeout == {
        "connect": expected_timeout,
        "read": expected_timeout,
        "write": expected_timeout,
        "pool": expected_pool,
    }
