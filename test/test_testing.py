"""Consumer test helpers: request matching and credential redaction."""

import httpx
import pytest

from aciClient import (
    AciClient,
    AciQuery,
    NexusDashboardPasswordAuth,
    PasswordAuth,
)
from aciClient.testing import (
    ExpectedRequest,
    ScriptedTransport,
    login_response,
    managed_object,
)


@pytest.mark.parametrize(
    "params",
    [
        {"subscription": True},
        {"subscription": False},
        {"repeated": ["a", "b"]},
        {"repeated": ("a", "a", "b")},
        [("repeated", "a"), ("repeated", "b"), ("page", 0)],
        AciQuery(subscription=True, refresh_timeout=140),
    ],
)
def test_expected_query_uses_the_same_normalization_as_the_client(params):
    transport = ScriptedTransport(
        [
            ExpectedRequest("POST", "/api/aaaLogin.json", response=login_response()),
            ExpectedRequest("GET", "/api/class/fvTenant.json", query=params),
        ]
    )
    with AciClient(
        "apic.example", PasswordAuth("u", "p"), transport=transport
    ) as client:
        assert client.get_class("fvTenant", params=params).is_empty
    transport.assert_all_consumed()


def test_expected_query_still_rejects_missing_repeated_values():
    transport = ScriptedTransport(
        [
            ExpectedRequest("POST", "/api/aaaLogin.json", response=login_response()),
            ExpectedRequest(
                "GET", "/api/class/fvTenant.json", query={"repeated": ["a", "b"]}
            ),
        ]
    )
    with (
        AciClient(
            "apic.example", PasswordAuth("u", "p"), transport=transport
        ) as client,
        pytest.raises(AssertionError, match="unexpected request"),
    ):
        client.get_class("fvTenant", params={"repeated": ["a"]})


@pytest.mark.parametrize(
    "field",
    [
        "key",
        "encPwd",
        "rootpwd",
        "userPasswd",
        "monitoringPassword",
        "private_key",
        "sharedSecret",
    ],
)
def test_history_redacts_apic_credentials_without_changing_the_sent_body(field):
    payload = managed_object(
        "aaaTacacsPlusProvider", {"name": "server", field: "sensitive-value"}
    )
    transport = ScriptedTransport(
        [
            ExpectedRequest("POST", "/api/aaaLogin.json", response=login_response()),
            ExpectedRequest("POST", "/api/mo.json", json=payload),
        ]
    )
    with AciClient(
        "apic.example", PasswordAuth("u", "p"), transport=transport
    ) as client:
        client.post_mo(payload)
    assert "sensitive-value" not in repr(transport.history)
    assert (
        transport.history[-1].json["aaaTacacsPlusProvider"]["attributes"]["name"]
        == "server"
    )
    assert payload["aaaTacacsPlusProvider"]["attributes"][field] == "sensitive-value"
    transport.assert_all_consumed()


def test_history_masks_sensitive_headers_query_parameters_and_filters():
    transport = ScriptedTransport([ExpectedRequest("POST", "/api/mo.json")])
    query = {
        "token": "query-secret",
        "query-target-filter": 'eq(aaaUser.pwd,"filter-secret")',
    }
    with httpx.Client(transport=transport) as http:
        http.post(
            "https://apic.example/api/mo.json",
            params=query,
            headers={
                "Proxy-Authorization": "proxy-secret",
                "X-API-Key": "api-secret",
                "Accept": "application/json",
            },
        )
    assert not any(
        secret in repr(transport.history)
        for secret in ["query-secret", "filter-secret", "proxy-secret", "api-secret"]
    )
    assert transport.history[-1].headers["accept"] == "application/json"


def test_history_masks_queries_embedded_in_gateway_paths():
    transport = ScriptedTransport(
        [
            ExpectedRequest(
                "POST", "/api/v1/infra/login", response={"token": "nd-token"}
            ),
            ExpectedRequest(
                "GET",
                "/api/v1/oneManage/apics/fabric/api/class/aaaUser.json?token=path-secret",
            ),
        ]
    )
    with AciClient(
        "nd.example",
        NexusDashboardPasswordAuth("u", "p"),
        nexus_dashboard_cluster_name="fabric",
        transport=transport,
    ) as client:
        client.get_class("aaaUser", params={"token": "path-secret"})
    assert "path-secret" not in repr(transport.history)
    transport.assert_all_consumed()
