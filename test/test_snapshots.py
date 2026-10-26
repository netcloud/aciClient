"""Tests for snapshots behavior."""

import httpx
import pytest

from aciClient import (
    AciClient,
    JobFailedError,
    JobState,
    JobTimeoutError,
    PasswordAuth,
)
from aciClient.snapshots import SnapshotJob, pending_job
from aciClient.testing import (
    ExpectedRequest,
    ManualClock,
    ScriptedTransport,
    imdata_response,
    login_response,
    snapshot_container_response,
    snapshot_job_response,
)

from .helpers import client_for, login_expectation, snapshot_client


def test_snapshot_submission_and_wait_are_distinct():
    transport = ScriptedTransport(
        [
            login_expectation(),
            ExpectedRequest(
                "GET", "/api/class/configJobCont.json", response=imdata_response()
            ),
            ExpectedRequest("POST", "/api/mo.json", response=imdata_response()),
            ExpectedRequest(
                "GET",
                "/api/class/configJobCont.json",
                response=snapshot_container_response(
                    "snap", snapshot_job_response("snap")["imdata"][0]
                ),
            ),
        ]
    )
    clock = ManualClock()
    client = AciClient(
        "apic.example",
        PasswordAuth("admin", "secret"),
        transport=transport,
        runtime=clock,
    )
    job = client.create_snapshot(name="snap")
    assert not job.done
    assert job.wait(timeout=10).job.state.value == "succeeded"


@pytest.mark.parametrize(
    "state", ["success", "success-with-warnings", "failed", "fail-no-data"]
)
def test_real_snapshot_container_discovery_states_and_context(state):
    client, new_dn = snapshot_client(state, reuse=True)
    with client:
        job = client.create_snapshot(
            name="snap", description="keep me", target_dn="uni/tn-test"
        )
        submitted = job.info.submitted_at
        assert job.info.job_dn is None
        if state in {"failed", "fail-no-data"}:
            with pytest.raises(JobFailedError) as error:
                job.wait(timeout=5)
            assert error.value.error_text == "export detail"
        else:
            result = job.wait(timeout=5)
            assert result.warnings == (
                ("export detail",) if state.endswith("warnings") else ()
            )
            assert result.completed_at.isoformat() == "2026-10-26T10:00:02+00:00"
        assert job.info.job_dn == new_dn
        assert job.info.policy_dn == "uni/fabric/configexp-snap"
        assert job.info.submitted_at == submitted
        assert job.info.description == "keep me" and job.info.target_dn == "uni/tn-test"
        assert job.done


def test_snapshot_timeout_only_polls_jobs_without_diagnostic_requests():
    requests = []
    client, _ = snapshot_client(missing=True, requests=requests)
    with client:
        job = client.create_snapshot(name="snap")
        original_info = job.info
        requests.clear()
        with pytest.raises(JobTimeoutError) as error:
            job.wait(timeout=1, poll_interval=0.5)
        assert "last state: pending" in str(error.value)
        assert error.value.raw == original_info.raw
        assert job.info == original_info
        # Poll at 0 and 0.5 seconds; do not send another request at the deadline.
        assert len(requests) == 2
        assert all(
            r.method == "GET" and r.url.path == "/api/class/configJobCont.json"
            for r in requests
        )
        assert job.state == JobState.PENDING and job.info.job_dn is None


def test_snapshot_unknown_submission_time_stays_unknown():
    from aciClient.models import ManagedObject
    from aciClient.snapshots import build_job_info

    raw = snapshot_job_response("snap")["imdata"][0]
    obj = ManagedObject.from_imdata(raw)
    first = build_job_info(obj, {"imdata": [raw]})
    refreshed = build_job_info(obj, {"imdata": [raw]}, first)
    assert first.submitted_at is None and refreshed.submitted_at is None
    assert first.policy_dn == refreshed.policy_dn == "uni/fabric/configexp-snap"


def test_snapshot_discovery_refuses_runs_from_another_policy():
    from aciClient.snapshots import discover_jobs

    body = snapshot_container_response(
        "foreign", snapshot_job_response("foreign")["imdata"][0]
    )

    def handler(request):
        return httpx.Response(
            200,
            json=login_response()
            if request.url.path.endswith("aaaLogin.json")
            else body,
        )

    with client_for(handler) as client:
        assert discover_jobs(client, "uni/fabric/configexp-snap") == ()


def test_snapshot_budget_includes_login_and_rejects_late_success():
    clock = ManualClock()
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("aaaLogin.json"):
            clock.advance(0.2)
            return httpx.Response(200, json=login_response())
        clock.advance(1)
        return httpx.Response(
            200,
            json=snapshot_container_response(
                "snap", snapshot_job_response("snap")["imdata"][0]
            ),
        )

    with AciClient(
        "apic.example",
        PasswordAuth("u", "p"),
        transport=httpx.MockTransport(handler),
        runtime=clock,
    ) as client:
        job = SnapshotJob(
            client, pending_job("uni/fabric/configexp-snap", "snap"), clock
        )
        with pytest.raises(JobTimeoutError):
            job.wait(timeout=1)
        assert not job.done
    assert set(requests[0].extensions["timeout"].values()) == {1}
    assert all(
        value == pytest.approx(0.8)
        for value in requests[1].extensions["timeout"].values()
    )


def test_zero_snapshot_budget_does_not_send_a_request():
    transport = ScriptedTransport()
    clock = ManualClock()
    with AciClient(
        "apic.example", PasswordAuth("u", "p"), transport=transport, runtime=clock
    ) as client:
        job = SnapshotJob(
            client, pending_job("uni/fabric/configexp-snap", "snap"), clock
        )
        with pytest.raises(JobTimeoutError):
            job.wait(timeout=0)
    assert transport.history == []
