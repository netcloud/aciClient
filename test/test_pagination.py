"""Tests for pagination behavior."""

import httpx
import pytest

from aciClient import (
    AciClient,
    AciTimeoutError,
    PaginationPolicy,
    PasswordAuth,
)
from aciClient.testing import (
    ExpectedRequest,
    ManualClock,
    ScriptedTransport,
    imdata_response,
    login_response,
    managed_object,
)

from .helpers import client_for, login_expectation


def test_pagination_replaces_page_and_honors_max_items():
    transport = ScriptedTransport(
        [
            login_expectation(),
            ExpectedRequest(
                "GET",
                "/api/class/fvTenant.json",
                query=[("page", "0"), ("page-size", "2")],
                response=imdata_response(
                    managed_object("fvTenant", {"name": "a"}),
                    managed_object("fvTenant", {"name": "b"}),
                ),
            ),
            ExpectedRequest(
                "GET",
                "/api/class/fvTenant.json",
                query=[("page", "1"), ("page-size", "2")],
                response=imdata_response(
                    managed_object("fvTenant", {"name": "c"}),
                    managed_object("fvTenant", {"name": "d"}),
                ),
            ),
        ]
    )
    client = AciClient(
        "apic.example", PasswordAuth("admin", "secret"), transport=transport
    )
    objects = client.collect_objects(
        "class/fvTenant.json", policy=PaginationPolicy(page_size=2, max_items=3)
    )
    assert [x.attributes.get("name") for x in objects] == ["a", "b", "c"]


def test_pagination_caps_wire_size_and_collects_beyond_first_cap():
    pages = []

    def handler(request):
        if request.url.path.endswith("aaaLogin.json"):
            return httpx.Response(200, json=login_response())
        assert request.url.params["page-size"] == "2000"
        page = int(request.url.params["page"])
        pages.append(page)
        size = 2000 if page < 2 else 1
        return httpx.Response(
            200,
            json=imdata_response(
                *[
                    managed_object("eventRecord", {"id": str(page * 2000 + i)})
                    for i in range(size)
                ],
                total_count=4001,
            ),
        )

    with client_for(handler) as client:
        values = client.collect_objects(
            "class/eventRecord.json", policy=PaginationPolicy(page_size=50000)
        )
        assert len(values) == 4001 and pages == [0, 1, 2]


def test_pagination_discards_late_pages_and_does_not_leak_budget():
    clock = ManualClock()
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("aaaLogin.json"):
            return httpx.Response(200, json=login_response())
        if "/class/" in request.url.path:
            clock.advance(2)
        return httpx.Response(
            200, json=imdata_response(managed_object("fvTenant", {"name": "a"}))
        )

    with AciClient(
        "apic.example",
        PasswordAuth("u", "p"),
        transport=httpx.MockTransport(handler),
        runtime=clock,
    ) as client:
        assert (
            client.collect_objects(
                "class/fvTenant.json", policy=PaginationPolicy(deadline=1)
            )
            == []
        )
        assert client.get_mo("uni/tn-a").one().attributes["name"] == "a"
    assert set(requests[-1].extensions["timeout"].values()) == {100}


def test_shorter_http_timeout_is_not_swallowed_by_a_longer_pagination_budget():
    def handler(request):
        if request.url.path.endswith("aaaLogin.json"):
            return httpx.Response(200, json=login_response())
        raise httpx.ReadTimeout("read stalled", request=request)

    with AciClient(
        "apic.example",
        PasswordAuth("u", "p"),
        transport=httpx.MockTransport(handler),
        runtime=ManualClock(),
    ) as client:
        with pytest.raises(AciTimeoutError, match="read stalled"):
            client.collect_objects(
                "class/fvTenant.json", timeout=0.5, policy=PaginationPolicy(deadline=10)
            )
