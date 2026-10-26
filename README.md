# aciClient 2.0

A lightweight, typed, thread-safe Python 3.12+ client for Cisco ACI automation
and pytest. Connect directly to APIC or through the Nexus Dashboard ACI gateway.

[Examples](https://github.com/netcloud/aciClient/blob/master/examples/README.md) · [Testing](https://github.com/netcloud/aciClient/blob/master/TESTING.md) · [Migration](https://github.com/netcloud/aciClient/blob/master/examples/README.md#legacy-migration) · [Contributing](https://github.com/netcloud/aciClient/blob/master/CONTRIBUTING.md)

## Installation

```bash
pip install aciClient
```

## Quick start

The examples use Cisco's shared
[Always-On ACI Simulator](https://developer.cisco.com/docs/aci/sandbox/) and
tenant `aciclient`.  
Run the [create example](https://github.com/netcloud/aciClient/blob/master/examples/README.md#create-a-bridge-domain-in-a-tenant)
first, then read the tenant using the public sandbox credentials:

```python
from aciClient import AciClient, PasswordAuth

with AciClient(
    "sandboxapicdc.cisco.com", PasswordAuth("admin", "!v3G@!4@Y")
) as client:
    tenant = client.get_mo("uni/tn-aciclient").one()
    print(tenant.attributes["name"])
```

Construction performs no network traffic. Login is lazy and coordinated
across threads; the context manager closes the client. `one()` raises
`NoResultError` when the tenant does not exist.

## Examples

| Task | Where to start |
| --- | --- |
| Create, read, update, or delete bridge domains | [Tenant management](https://github.com/netcloud/aciClient/blob/master/examples/README.md#tenant-management) |
| Work with child objects and APIC filter operators | [Children and filtering](https://github.com/netcloud/aciClient/blob/master/examples/README.md#read-a-tenants-child-bridge-domains) |
| Integrate a shared client into an application or FastAPI | [Client lifecycle](https://github.com/netcloud/aciClient/blob/master/examples/README.md#client-lifecycle) |
| Receive APIC notifications with an external WebSocket | [Subscription workflow](https://github.com/netcloud/aciClient/blob/master/examples/README.md#subscriptions) |
| Test automation with an injected HTTP transport | [Pytest guide](https://github.com/netcloud/aciClient/blob/master/TESTING.md) |

## Nexus Dashboard gateway

Nexus Dashboard provides an
[APIC API Proxy](https://developer.cisco.com/docs/nexus-dashboard/latest/get-apic-data/).
For ordinary APIC requests, change only client creation: use the Nexus
Dashboard IP or hostname as `endpoint` and the ACI cluster name shown in
Nexus Dashboard as `nexus_dashboard_cluster_name`. Request methods, DNs,
payloads, filters, responses, and pagination stay the same.

```python
from aciClient import AciClient, NexusDashboardPasswordAuth

client = AciClient(
    endpoint="nd.example.test",
    auth=NexusDashboardPasswordAuth("admin", "secret"),
    nexus_dashboard_cluster_name="production-fabric",
)
```

Gateway access requires an enabled APIC Proxy Mode and a Super-Admin account.
Only `NexusDashboardPasswordAuth` is supported in this mode; subscriptions
are unavailable. See [gateway setup and limitations](https://github.com/netcloud/aciClient/blob/master/examples/README.md#nexus-dashboard-gateway)
for authentication, per-cluster clients, and session lifecycle details.

## Client behavior

| Area | What to expect |
| --- | --- |
| Authentication | Direct APIC supports `PasswordAuth` and `CertificateAuth`. See [domains and certificate keys](https://github.com/netcloud/aciClient/blob/master/examples/README.md#authentication-and-tls). |
| TLS | Verification is enabled by default. CA paths and `SSLContext` are supported; `verify=False` emits `InsecureTLSWarning`. |
| Results | Methods return `AciResponse` with immutable generic `ManagedObject` objects. Unknown classes and attributes are preserved; APIC attribute values remain strings. |
| Writes | Build explicit payloads: read objects may contain blank sensitive attributes. |
| Pagination | Requests do not paginate automatically. Use `iter_pages`, `iter_objects`, `collect_pages`, or `collect_objects` with `PaginationPolicy`; effective page size is capped at 2,000. [Limits and consistency](https://github.com/netcloud/aciClient/blob/master/examples/README.md#responses-and-pagination). |
| Connections | Share one client per connection identity across threads and create separate clients per process. Applications own shutdown and registries. [Lifecycle details](https://github.com/netcloud/aciClient/blob/master/examples/README.md#client-lifecycle). |
| Retries | Each request is sent once. Applications own retries, queuing, and metrics. |
| Snapshots | `create_snapshot()` returns a job; `job.wait()` waits for completion. Secure fields are excluded by default. [Snapshot example](https://github.com/netcloud/aciClient/blob/master/examples/README.md#snapshots). |

## Testing with pytest

Test your automation offline using scripted APIC responses. Start with the
[runnable pytest example](https://github.com/netcloud/aciClient/blob/master/examples/test_automation.py), or see the
[testing guide](https://github.com/netcloud/aciClient/blob/master/TESTING.md) for fixtures and error handling.

## Legacy migration

`ACI` and `ACICert` remain available in 2.0 and emit `LegacyAPIWarning`.
Version 2.1, targeted for October 2027, will require explicit
`allow_legacy=True`. See the [migration guide](https://github.com/netcloud/aciClient/blob/master/examples/README.md#legacy-migration)
for compatibility details, method replacements, and the removal timeline.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
```

Repository tests use mocked transports and require no APIC or Nexus Dashboard.
See [CONTRIBUTING.md](https://github.com/netcloud/aciClient/blob/master/CONTRIBUTING.md) for contribution and release procedures.
