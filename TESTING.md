# Testing with pytest

`AciClient` accepts an `httpx` transport, so tests can exercise the real client
without contacting an APIC. The helpers in `aciClient.testing` provide scripted
requests and common APIC response bodies.

These examples use the same public Cisco sandbox endpoint and credentials as
the [README](README.md#quick-start). Injected transports keep all requests
offline; running these tests does not access or change the shared sandbox.

## A reusable fixture

```python
import pytest

from aciClient import AciClient, PasswordAuth
from aciClient.testing import (
    ExpectedRequest,
    ScriptedTransport,
    imdata_response,
    login_response,
    managed_object,
)


@pytest.fixture
def tenant_client():
    transport = ScriptedTransport(
        [
            ExpectedRequest(
                "POST",
                "/api/aaaLogin.json",
                json={
                    "aaaUser": {
                        "attributes": {"name": "admin", "pwd": "!v3G@!4@Y"}
                    }
                },
                response=login_response(),
            ),
            ExpectedRequest(
                "GET",
                "/api/mo/uni/tn-aciclient.json",
                response=imdata_response(
                    managed_object(
                        "fvTenant", {"dn": "uni/tn-aciclient", "name": "aciclient"}
                    )
                ),
            ),
        ]
    )

    with AciClient(
        "sandboxapicdc.cisco.com",
        PasswordAuth("admin", "!v3G@!4@Y"),
        transport=transport,
    ) as client:
        yield client, transport

    transport.assert_all_consumed()


def test_reads_tenant(tenant_client):
    client, transport = tenant_client

    tenant = client.get_mo("uni/tn-aciclient").one()

    assert tenant.dn == "uni/tn-aciclient"
    assert tenant.attributes["name"] == "aciclient"
    assert transport.history[-1].path == "/api/mo/uni/tn-aciclient.json"
```

`ScriptedTransport` checks the method, path, query parameters, selected headers,
and JSON body. Calls are matched in order by default. An unexpected request
raises `AssertionError` instead of reaching the network, and
`assert_all_consumed()` detects expected calls that never happened.

`ExpectedRequest.query` accepts the same `AciQuery`, mapping, or sequence of
pairs as the client's `params` argument. Boolean values and repeated query
parameters use the same serialization in requests and expectations.

Recorded authorization headers, cookies, passwords, tokens, and APIC shared
keys are redacted by credential field name, including nested JSON attributes
and named query parameters. Filters mentioning credential fields are masked,
as are credential queries embedded in Nexus Dashboard proxy paths. Matching
still uses the original request. Secrets placed in free text or unrelated
fields are not detected automatically; avoid including those in assertion
messages. Closing `AciClient` also closes the injected transport.

## Testing API errors

Use `api_error_response()` to build the error shape returned by APIC:

```python
import pytest

from aciClient import AciApiError, AciClient, PasswordAuth
from aciClient.testing import (
    ExpectedRequest,
    ScriptedTransport,
    api_error_response,
    login_response,
)


def test_create_tenant_reports_apic_error():
    transport = ScriptedTransport(
        [
            ExpectedRequest(
                "POST",
                "/api/aaaLogin.json",
                response=login_response(),
            ),
            ExpectedRequest(
                "POST",
                "/api/mo.json",
                response=api_error_response("103", "invalid tenant"),
            ),
        ]
    )

    with AciClient(
        "sandboxapicdc.cisco.com",
        PasswordAuth("admin", "!v3G@!4@Y"),
        transport=transport,
    ) as client, pytest.raises(AciApiError, match="invalid tenant"):
        client.post_mo(
            {"fvTenant": {"attributes": {"dn": "uni/tn-aciclient", "name": "aciclient"}}}
        )
```

An `ExpectedRequest` can return an exception such as `httpx.ConnectError` to
test transport failures. Set `repeat` when the same request is expected more
than once, or create `ScriptedTransport(..., ordered=False)` when call order is
irrelevant.

## Dynamic responses

For behavior that is easier to express in code, pass an `httpx.MockTransport`:

```python
import httpx

from aciClient import AciClient, PasswordAuth
from aciClient.testing import imdata_response, login_response


def handler(request: httpx.Request) -> httpx.Response:
    payload = (
        login_response()
        if request.url.path.endswith("/aaaLogin.json")
        else imdata_response()
    )
    return httpx.Response(200, json=payload, request=request)


client = AciClient(
    "sandboxapicdc.cisco.com",
    PasswordAuth("admin", "!v3G@!4@Y"),
    transport=httpx.MockTransport(handler),
)
```

Use `ScriptedTransport` for most tests because its expected request sequence is
short and explicit. `MockTransport` is useful for stateful or highly dynamic
test behavior.
