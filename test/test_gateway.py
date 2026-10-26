"""Tests for gateway behavior."""

import httpx
import pytest

from aciClient import (
    AciApiError,
    AciClient,
    NexusDashboardPasswordAuth,
)
from aciClient.testing import (
    ExpectedRequest,
    ScriptedTransport,
    imdata_response,
)

from .helpers import client_for


def test_gateway_routing_embeds_apic_query_in_proxy_path():
    transport = ScriptedTransport(
        [
            ExpectedRequest(
                "POST",
                "/api/v1/infra/login",
                json={
                    "domain": "local",
                    "userName": "admin",
                    "userPasswd": "secret",
                },
                response={"token": "test-token", "statusCode": 200},
            ),
            ExpectedRequest(
                "GET",
                "/api/v1/oneManage/apics/prod fabric/api/class/fvTenant.json?page=0",
                query=[],
                headers={"Cookie": "AuthCookie=test-token"},
                response=imdata_response(),
            ),
        ]
    )
    client = AciClient(
        "https://nd.example:8443/api",
        NexusDashboardPasswordAuth("admin", "secret"),
        nexus_dashboard_cluster_name="prod fabric",
        transport=transport,
    )
    client.get_class("fvTenant", params={"page": 0})
    transport.assert_all_consumed()


def test_nd_error_code_message_and_raw_are_retained():
    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"token": "test-token"})
        return httpx.Response(
            400,
            json={
                "code": 4065,
                "message": "bad proxy request",
                "description": "details",
            },
        )

    with client_for(handler, gateway=True) as client:
        with pytest.raises(AciApiError) as error:
            client.get_class("fvTenant")
        assert error.value.error_code == "4065"
        assert error.value.error_text == "bad proxy request"
        assert error.value.raw["description"] == "details"


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_nd_apic_query_is_inside_proxy_path(method):
    from urllib.parse import unquote

    sent = []

    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"token": "nd-token"})
        sent.append(request)
        return httpx.Response(200, json=imdata_response())

    with client_for(handler, gateway=True) as client:
        client._request(
            method,
            "class/fvTenant.json",
            params=[
                ("query-target-filter", 'eq(fvTenant.name,"common")'),
                ("page-size", "2"),
                ("repeated", "a"),
                ("repeated", "b"),
            ],
        )
    request = sent[0]
    assert request.method == method and not request.url.query
    target = request.url.raw_path.decode("ascii")
    assert (
        "/api/class/fvTenant.json%3Fquery-target-filter=eq%28fvTenant.name%2C%22common%22%29"
        in target
    )
    inner_path = unquote(target.split("/apics/fabric/", 1)[1])
    assert (
        inner_path
        == 'api/class/fvTenant.json?query-target-filter=eq(fvTenant.name,"common")&page-size=2&repeated=a&repeated=b'
    )
