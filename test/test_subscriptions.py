"""Tests for subscriptions behavior."""

import httpx
import pytest

from aciClient import (
    AciClient,
    AciQuery,
    NexusDashboardPasswordAuth,
    PasswordAuth,
    ResponseDecodeError,
)
from aciClient.exceptions import ConfigurationError
from aciClient.testing import (
    ExpectedRequest,
    ScriptedTransport,
    login_response,
    subscription_response,
)


def test_gateway_subscription_is_rejected_before_request():
    transport = ScriptedTransport()
    gateway = AciClient(
        "nd.example",
        NexusDashboardPasswordAuth("id", "secret"),
        nexus_dashboard_cluster_name="c",
        transport=transport,
    )
    with pytest.raises(ConfigurationError):
        gateway.subscribe_class("fvTenant")


@pytest.mark.parametrize(
    "params, override, expected_timeout",
    [
        (AciQuery(refresh_timeout=140), None, 140),
        ({"refresh-timeout": 140}, None, 140),
        ([("refresh-timeout", "140")], None, 140),
        (AciQuery(refresh_timeout=140), 90, 90),
        (None, 90, 90),
        (None, None, None),
    ],
)
def test_subscription_preserves_query_timeout_unless_overridden(
    params, override, expected_timeout
):
    query = {"subscription": "yes"}
    if expected_timeout is not None:
        query["refresh-timeout"] = str(expected_timeout)
    transport = ScriptedTransport(
        [
            ExpectedRequest("POST", "/api/aaaLogin.json", response=login_response()),
            ExpectedRequest(
                "GET",
                "/api/class/fvTenant.json",
                query=query,
                response=subscription_response(),
            ),
        ]
    )
    with AciClient(
        "apic.example", PasswordAuth("u", "p"), transport=transport
    ) as client:
        subscription = client.subscribe_class(
            "fvTenant", params=params, refresh_timeout=override
        )
        assert subscription.refresh_timeout == expected_timeout
    transport.assert_all_consumed()


@pytest.mark.parametrize("subscription_id", [None, "", " ", [], {}, True])
def test_missing_or_invalid_subscription_ids_are_decode_errors(subscription_id):
    def handler(request):
        payload = (
            login_response()
            if request.url.path.endswith("aaaLogin.json")
            else {"imdata": [], "subscriptionId": subscription_id}
        )
        return httpx.Response(200, json=payload)

    with AciClient(
        "apic.example", PasswordAuth("u", "p"), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ResponseDecodeError, match="subscriptionId"):
            client.subscribe_class("fvTenant")
