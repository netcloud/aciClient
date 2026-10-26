"""Snapshot run discovery, state handling, and bounded completion polling."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ._timeouts import Deadline, remaining_request_time
from .exceptions import (
    AciTimeoutError,
    JobFailedError,
    JobTimeoutError,
    ResponseDecodeError,
)
from .models import JobState, SnapshotJobInfo, SnapshotResult
from .query import eq

if TYPE_CHECKING:
    from .client import AciClient
    from .models import ManagedObject
    from .runtime import SyncRuntime


def _state(value: Any) -> JobState:
    normalized = str(value or "").lower()
    if normalized in {
        "completed",
        "complete",
        "success",
        "succeeded",
        "success-with-warnings",
    }:
        return JobState.SUCCEEDED
    if normalized in {"failed", "failure", "error", "fail-no-data"}:
        return JobState.FAILED
    if normalized in {"running", "inprogress", "processing"}:
        return JobState.RUNNING
    if normalized in {"pending", "queued", "created"}:
        return JobState.PENDING
    return JobState.UNKNOWN


def _timestamp(value: Any) -> datetime | None:
    if not value or value == "never":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def pending_job(policy_dn: str, name: str) -> SnapshotJobInfo:
    return SnapshotJobInfo(
        policy_dn=policy_dn,
        job_dn=None,
        name=name,
        description="",
        target_dn=None,
        submitted_at=None,
        state=JobState.PENDING,
        attributes={},
        raw={},
    )


def discover_jobs(client: AciClient, policy_dn: str) -> tuple[ManagedObject, ...]:
    """Read returned runs through their policy-associated container, not a guessed run DN."""
    response = client.get_class(
        "configJobCont",
        params={
            "query-target-filter": str(eq("configJobCont.pDn", policy_dn)),
            "rsp-subtree": "full",
        },
    )
    jobs = []
    for container in response.objects_of("configJobCont"):
        if container.attributes.get("pDn") != policy_dn:
            continue
        for job in container.children_of("configJob"):
            if not job.dn:
                rn = job.attributes.get("rn")
                if not container.dn or not isinstance(rn, str) or not rn:
                    raise ResponseDecodeError(
                        "snapshot child requires a returned parent DN and RN"
                    )
                job = replace(
                    job, attributes={**job.attributes, "dn": container.dn + "/" + rn}
                )
            jobs.append(job)
    return tuple(jobs)


def build_job_info(
    obj: ManagedObject, raw: Mapping[str, Any], context: SnapshotJobInfo | None = None
) -> SnapshotJobInfo:
    if obj.class_name != "configJob" or not obj.dn:
        raise ResponseDecodeError(
            "snapshot response must contain a configJob with a DN"
        )
    attributes = obj.attributes
    match = re.search(r"/jobs-\[(.*)\]/", obj.dn)
    policy_dn = str(
        attributes.get("policyDn")
        or (context.policy_dn if context else "")
        or (match.group(1) if match else "")
    )
    if not policy_dn:
        raise ResponseDecodeError("snapshot run does not identify its export policy")
    return SnapshotJobInfo(
        policy_dn=policy_dn,
        job_dn=obj.dn,
        name=str(attributes.get("name") or obj.dn.rsplit("/", 1)[-1]),
        description=context.description
        if context
        else str(attributes.get("descr") or ""),
        target_dn=context.target_dn if context else attributes.get("targetDn"),
        submitted_at=context.submitted_at
        if context and context.submitted_at
        else _timestamp(attributes.get("executeTime")),
        state=_state(attributes.get("operSt")),
        attributes=attributes,
        raw=raw,
    )


def _result(info: SnapshotJobInfo) -> SnapshotResult:
    warnings = ()
    if str(info.attributes.get("operSt", "")).lower() == "success-with-warnings":
        warnings = (
            str(
                info.attributes.get("details") or "APIC export completed with warnings"
            ),
        )
    failed = info.state == JobState.FAILED
    return SnapshotResult(
        job=info,
        snapshot_name=info.attributes.get("snapshotName")
        or info.attributes.get("fileName"),
        completed_at=_timestamp(info.attributes.get("lastStepTime"))
        if info.state in {JobState.SUCCEEDED, JobState.FAILED}
        else None,
        error_code=info.attributes.get("errorCode") if failed else None,
        error_text=(info.attributes.get("errorText") or info.attributes.get("details"))
        if failed
        else None,
        raw=info.raw,
        warnings=warnings,
    )


class SnapshotJob:
    def __init__(
        self,
        client: AciClient,
        info: SnapshotJobInfo,
        runtime: SyncRuntime,
        *,
        baseline: frozenset[str | None] = frozenset(),
    ) -> None:
        self._client = client
        self._runtime = runtime
        self._baseline = baseline
        self.info = info

    @property
    def done(self) -> bool:
        return self.state in {JobState.SUCCEEDED, JobState.FAILED}

    @property
    def state(self) -> JobState:
        return self.info.state

    def refresh(self) -> SnapshotJobInfo:
        if self.info.job_dn:
            response = self._client.get_mo(self.info.job_dn)
            obj = response.first()
            if obj:
                self.info = build_job_info(obj, response.raw, self.info)
        else:
            jobs = [
                obj
                for obj in discover_jobs(self._client, self.info.policy_dn)
                if obj.dn not in self._baseline
            ]
            if jobs:
                obj = max(
                    jobs,
                    key=lambda x: str(
                        x.attributes.get("executeTime")
                        or x.attributes.get("name")
                        or ""
                    ),
                )
                self.info = build_job_info(obj, {"imdata": [dict(obj.raw)]}, self.info)
        return self.info

    def wait(self, timeout: float = 120, poll_interval: float = 2) -> SnapshotResult:
        """Poll until the export succeeds, fails, or reaches the timeout."""
        if (
            not math.isfinite(timeout)
            or timeout < 0
            or not math.isfinite(poll_interval)
            or poll_interval <= 0
        ):
            raise ValueError(
                "timeout must be finite and non-negative; poll_interval finite and positive"
            )
        deadline = Deadline(self._runtime, timeout)
        try:
            with deadline.activate():
                while True:
                    remaining_request_time()
                    self.refresh()
                    if self.done:
                        result = _result(self.info)
                        if self.state == JobState.SUCCEEDED:
                            return result
                        raise JobFailedError(
                            result.error_text or "snapshot job failed",
                            job_dn=self.info.job_dn,
                            job_name=self.info.name,
                            final_state=self.state,
                            error_code=result.error_code,
                            error_text=result.error_text,
                            raw=result.raw,
                        )
                    self._runtime.sleep(min(poll_interval, deadline.remaining))
        except AciTimeoutError as exc:
            if deadline.remaining > 0:
                raise
            raise JobTimeoutError(
                f"snapshot {self.info.policy_dn!r} did not complete within {timeout}s "
                f"(last state: {self.state.value})",
                raw=self.info.raw,
            ) from exc
