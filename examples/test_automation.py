"""Test an automation function offline: python -m pytest -q examples/test_automation.py."""

import pytest

from aciClient import AciClient, PasswordAuth
from aciClient.testing import (
    ExpectedRequest,
    ScriptedTransport,
    imdata_response,
    login_response,
    managed_object,
)


def tenant_names(client: AciClient) -> list[str]:
    """Application code: return tenant names in a predictable order."""
    tenants = client.get_class("fvTenant").objects
    return sorted(tenant.attributes["name"] for tenant in tenants)


@pytest.fixture
def tenant_client():
    transport = ScriptedTransport(
        [
            ExpectedRequest("POST", "/api/aaaLogin.json", response=login_response()),
            ExpectedRequest(
                "GET",
                "/api/class/fvTenant.json",
                response=imdata_response(
                    managed_object("fvTenant", {"name": "prod"}),
                    managed_object("fvTenant", {"name": "dev"}),
                ),
            ),
        ]
    )
    with AciClient(
        "apic.example.test",
        PasswordAuth("admin", "test-password"),
        transport=transport,
    ) as client:
        yield client
    transport.assert_all_consumed()


def test_tenant_names_are_sorted(tenant_client):
    assert tenant_names(tenant_client) == ["dev", "prod"]
