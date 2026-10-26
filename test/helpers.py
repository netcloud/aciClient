"""Shared factories for deterministic client and snapshot tests."""

import base64
import json

import httpx

from aciClient import (
    AciClient,
    NexusDashboardPasswordAuth,
    PasswordAuth,
)
from aciClient.testing import (
    ExpectedRequest,
    ManualClock,
    imdata_response,
    login_response,
    snapshot_container_response,
    snapshot_job_response,
)


def client_for(handler, *, clock=None, gateway=False):
    return AciClient(
        "apic.example",
        NexusDashboardPasswordAuth("u", "p") if gateway else PasswordAuth("u", "p"),
        nexus_dashboard_cluster_name="fabric" if gateway else None,
        transport=httpx.MockTransport(handler),
        runtime=clock,
    )


def jwt(exp):
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    )
    return "header." + payload + ".signature"


def login_expectation(username="admin", password="secret", domain=None):
    name = f"apic:{domain}\\{username}" if domain else username
    return ExpectedRequest(
        "POST",
        "/api/aaaLogin.json",
        json={"aaaUser": {"attributes": {"name": name, "pwd": password}}},
        response=login_response(),
    )


def snapshot_client(state="success", *, reuse=False, missing=False, requests=None):
    count = 0
    old_dn = "uni/backupst/jobs-[uni/fabric/configexp-snap]/run-2026-10-25T10-00-00"
    new_dn = "uni/backupst/jobs-[uni/fabric/configexp-snap]/run-2026-10-26T10-00-00"

    def handler(request):
        nonlocal count
        if requests is not None:
            requests.append(request)
        if request.url.path.endswith("aaaLogin.json"):
            return httpx.Response(200, json=login_response())
        if request.url.path == "/api/class/configJobCont.json":
            assert (
                request.url.params["query-target-filter"]
                == 'eq(configJobCont.pDn,"uni/fabric/configexp-snap")'
            )
            count += 1
            jobs = []
            if reuse:
                jobs.append(snapshot_job_response("snap", dn=old_dn)["imdata"][0])
            if count > 1 and not missing:
                jobs.append(
                    snapshot_job_response(
                        "snap",
                        state,
                        dn=new_dn,
                        details="export detail",
                        executeTime="2026-10-26T10:00:00+00:00",
                        lastStepTime="2026-10-26T10:00:02+00:00",
                    )["imdata"][0]
                )
            return httpx.Response(200, json=snapshot_container_response("snap", *jobs))
        return httpx.Response(200, json=imdata_response())

    return client_for(handler, clock=ManualClock()), new_dn
