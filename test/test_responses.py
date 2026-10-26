"""Response decoding, malformed server data, and structured API errors."""

import httpx
import pytest

from aciClient import (
    AciApiError,
    AciClient,
    AuthenticationError,
    NexusDashboardPasswordAuth,
    PasswordAuth,
    ResponseDecodeError,
)
from aciClient.testing import (
    ExpectedRequest,
    ScriptedTransport,
    api_error_response,
    login_response,
)


def test_dataset_limit_uses_api_error():
    transport = ScriptedTransport(
        [
            ExpectedRequest(
                "POST",
                "/api/v1/infra/login",
                json={
                    "domain": "local",
                    "userName": "id",
                    "userPasswd": "secret",
                },
                response={"token": "test-token", "statusCode": 200},
            ),
            ExpectedRequest(
                "GET",
                "/api/v1/oneManage/apics/c/api/class/fvBD.json",
                headers={"Cookie": "AuthCookie=test-token"},
                response=httpx.Response(
                    400,
                    json=api_error_response(
                        "103", "Unable to process query, result dataset is too big"
                    ),
                ),
            ),
        ]
    )
    client = AciClient(
        "nd.example",
        NexusDashboardPasswordAuth("id", "secret"),
        nexus_dashboard_cluster_name="c",
        transport=transport,
    )
    with pytest.raises(AciApiError):
        client.get_class("fvBD")


@pytest.mark.parametrize(
    "payload",
    [
        {"imdata": False},
        {"imdata": {}},
        {"imdata": [None]},
    ],
)
def test_invalid_envelope_is_a_decode_error(payload):
    def handler(request):
        return httpx.Response(
            200,
            json=login_response()
            if request.url.path.endswith("aaaLogin.json")
            else payload,
        )

    with AciClient(
        "apic.example", PasswordAuth("u", "p"), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ResponseDecodeError):
            client.get_class("fvTenant")


@pytest.mark.parametrize(
    "field,value",
    [
        ("attributes", 5),
        ("attributes", "invalid"),
        ("attributes", []),
        ("attributes", None),
        ("children", {}),
        ("children", "invalid"),
        ("children", [5]),
    ],
)
def test_invalid_managed_object_fields_are_decode_errors(field, value):
    payload = {"imdata": [{"fvTenant": {field: value}}]}

    def handler(request):
        return httpx.Response(
            200,
            json=login_response()
            if request.url.path.endswith("aaaLogin.json")
            else payload,
        )

    with AciClient(
        "apic.example", PasswordAuth("u", "p"), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ResponseDecodeError):
            client.get_class("fvTenant")


@pytest.mark.parametrize(
    "payload",
    [
        {"imdata": [5]},
        {"imdata": [{"aaaLogin": []}]},
        {"imdata": [{"aaaLogin": {"attributes": 5}}]},
        {"imdata": [{"aaaLogin": {"attributes": {"token": {"value": "secret"}}}}]},
    ],
)
def test_malformed_login_is_a_decode_error_and_clears_session(payload):
    with AciClient(
        "apic.example",
        PasswordAuth("u", "p"),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)),
    ) as client:
        with pytest.raises(ResponseDecodeError):
            client.login()
        assert not client.is_authenticated
        assert client.session_token is None


@pytest.mark.parametrize(
    "gateway,payload",
    [
        (False, {"imdata": [{"aaaDomain": {"attributes": []}}]}),
        (True, {"domains": [5]}),
        (True, {"domains": "invalid"}),
    ],
)
def test_malformed_domains_are_decode_errors(gateway, payload):
    auth = NexusDashboardPasswordAuth("u", "p") if gateway else PasswordAuth("u", "p")
    with AciClient(
        "apic.example",
        auth,
        nexus_dashboard_cluster_name="fabric" if gateway else None,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)),
    ) as client:
        with pytest.raises(ResponseDecodeError):
            client.list_login_domains()


def test_http_authentication_error_survives_a_malformed_error_body():
    with AciClient(
        "apic.example",
        PasswordAuth("u", "p"),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(401, json={"imdata": 5})
        ),
    ) as client:
        with pytest.raises(AuthenticationError):
            client.login()
