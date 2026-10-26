# Examples and usage notes

[Back to the main README](../README.md)

All tenant examples use `aciclient`. Start with the create example, then read
and update its bridge domain before deleting it.

| Topic | Guide |
| --- | --- |
| Tenant and bridge-domain operations | [Create](#create-a-bridge-domain-in-a-tenant), [read](#read-a-tenant), [update](#update-a-bridge-domain-in-a-tenant), [delete](#delete-a-bridge-domain-from-a-tenant) |
| Child objects and filters | [Read child bridge domains](#read-a-tenants-child-bridge-domains) |
| Response models and pagination | [Response handling](#responses-and-pagination) |
| Authentication and TLS | [Connection settings](#authentication-and-tls) |
| Nexus Dashboard | [Gateway setup](#nexus-dashboard-gateway) and [limitations](#gateway-limitations) |
| Application integration | [Client lifecycle](#client-lifecycle), including FastAPI |
| Event notifications | [Subscriptions](#subscriptions) and [runnable script](subscription.py) |
| Configuration backups | [Snapshots](#snapshots) |
| Test an automation function with pytest | [Offline testing](#testing-and-observability) and [runnable example](test_automation.py) |
| Migration | [Legacy interfaces](#legacy-migration) |

## Tenant management

These standalone examples use Cisco's shared
[Always-On ACI Simulator](https://developer.cisco.com/docs/aci/sandbox/)
with its public credentials:

- Endpoint: [https://sandboxapicdc.cisco.com](https://sandboxapicdc.cisco.com)
- Username: `admin`
- Password: `!v3G@!4@Y`

[Install aciClient](../README.md#installation), then run the create example
first to set up tenant `aciclient` and bridge domain `web`. Read and update
them before trying the delete example. The subscription example also uses
this tenant.

### Create a bridge domain in a tenant

Create tenant `aciclient` with bridge domain `web` as a child in one request.
The `children` list contains the managed objects to create under the tenant:

```python
from aciClient import AciClient, PasswordAuth

with AciClient(
    "sandboxapicdc.cisco.com", PasswordAuth("admin", "!v3G@!4@Y")
) as client:
    client.post_mo(
        {
            "fvTenant": {
                "attributes": {"name": "aciclient"},
                "children": [
                    {"fvBD": {"attributes": {"name": "web"}}},
                ],
            }
        },
        dn="uni/tn-aciclient",
    )
```

### Read a tenant

```python
from aciClient import AciClient, PasswordAuth

with AciClient(
    "sandboxapicdc.cisco.com", PasswordAuth("admin", "!v3G@!4@Y")
) as client:
    tenant = client.get_mo("uni/tn-aciclient").one()
    print(tenant.attributes["name"])
```

`one()` raises `NoResultError` if the tenant does not exist.

### Read a tenant's child bridge domains

Request the tenant together with its direct bridge-domain children, then
read each child's attributes:

```python
from aciClient import AciClient, AciQuery, PasswordAuth

with AciClient(
    "sandboxapicdc.cisco.com", PasswordAuth("admin", "!v3G@!4@Y")
) as client:
    tenant = client.get_mo(
        "uni/tn-aciclient",
        params=AciQuery(rsp_subtree="children", rsp_subtree_class=("fvBD",)),
    ).one()
    for bridge_domain in tenant.children_of("fvBD"):
        print(bridge_domain.attributes["name"], bridge_domain.dn)
```

`rsp_subtree="children"` includes direct children under the returned tenant.
`children_of("fvBD")` selects bridge-domain children, and `tenant.children`
gives all returned direct children. To request deeper descendants, use
`rsp_subtree="full"` and traverse each object's `children`.

See the [Cisco APIC REST API filtering documentation](https://www.cisco.com/c/en/us/td/docs/dcn/aci/apic/all/apic-rest-api-configuration-guide/cisco-apic-rest-api-configuration-guide/m_using_the_rest_api.html)
for query scoping, child inclusion, and filter expressions. The same APIC
filter operators are available in aciClient through Python helpers such as
`eq()`, `ne()`, `and_()`, `or_()`, and `not_()`. Some helpers have descriptive
Python names: `between()` corresponds to APIC's `bw`, and `wildcard()` to
`wcard`. Pass these expressions to `AciQuery(query_target_filter=...)` to
filter query results or `AciQuery(rsp_subtree_filter=...)` to filter returned
children. Query parameter names use underscores in Python, such as
`rsp_subtree` for APIC's `rsp-subtree`.

### Update a bridge domain in a tenant

Update the description of the existing bridge domain `web` in tenant `aciclient`:

```python
from aciClient import AciClient, PasswordAuth

with AciClient(
    "sandboxapicdc.cisco.com", PasswordAuth("admin", "!v3G@!4@Y")
) as client:
    client.post_mo(
        {"fvBD": {"attributes": {"descr": "Bridge domain for web servers"}}},
        dn="uni/tn-aciclient/BD-web",
    )
```

### Delete a bridge domain from a tenant

Delete `web` from tenant `aciclient`:

```python
from aciClient import AciClient, PasswordAuth

with AciClient(
    "sandboxapicdc.cisco.com", PasswordAuth("admin", "!v3G@!4@Y")
) as client:
    client.delete_mo("uni/tn-aciclient/BD-web")
```

### Responses and pagination

Modern methods return `AciResponse`, whose `objects` are immutable generic
`ManagedObject` instances. Unknown MIM classes and attributes are preserved.
Use `first()`, `one()`, `objects_of()`, `children_of()`, and `to_imdata()` for
typed access. `get_imdata()` returns the gradual-migration `list[dict]` shape.

APIC attributes remain strings. A read object can contain blank sensitive
properties, so do not blindly post a read object's `to_imdata()` result.

Normal requests do not silently paginate. Use `iter_pages`, `iter_objects`,
`collect_pages`, or `collect_objects` with `PaginationPolicy`. Pagination is
limited to an effective page size of 2,000, even when a larger size is supplied,
to accommodate APIC log-query caps. Termination uses that effective size. It is
zero-based and is not a stable snapshot: objects can be duplicated, omitted,
or changed between requests. APIC limits result datasets and event records to
100,000 objects and query execution to 90 seconds; the default read timeout is
100 seconds.

`PaginationPolicy.deadline` is an elapsed-time budget in seconds. It includes
login, refresh, waiting for authentication, and page requests. Iteration stops
when it expires and does not yield pages that arrive late. Each HTTP phase,
including connection-pool acquisition, is capped at the remaining budget.
HTTPX uses phase timeouts rather than forcibly cancelling a synchronous call,
so an in-flight request can still return after the budget; its result is
discarded. A custom transport must enforce the supplied HTTP timeouts itself.

## Authentication and TLS

`domain="LDAP"` sends the direct-APIC username as `apic:LDAP\\user`. An
already-qualified username is preserved when `domain` is omitted, and double
qualification is rejected. `list_login_domains()` uses the platform's domain
API without authentication, including when a session already exists. ND's
`defaultDomain` and raw per-domain `passwordSettings` are preserved.

Certificate authentication uses `CertificateAuth`; signatures are generated
with `cryptography` over the exact method, routed path/query, and serialized
body bytes. Private keys may be paths, PEM bytes, or compatible key objects.

Modern clients verify TLS by default and accept a CA path or `SSLContext`.
`verify=False` emits `InsecureTLSWarning`. The legacy clients preserve their
insecure behavior in 2.0; verified TLS should be used for production.

## Nexus Dashboard gateway

Nexus Dashboard provides ACI gateway functionality through its
[APIC API Proxy](https://developer.cisco.com/docs/nexus-dashboard/latest/get-apic-data/),
which forwards APIC API requests to a managed ACI fabric.

When moving an existing aciClient script from direct APIC access to Nexus
Dashboard, change only client creation. The gateway client is a drop-in
replacement for ordinary APIC requests: methods, DNs, payloads, query filters,
response objects, and pagination stay the same, subject to the limitations
below. aciClient handles authentication and gateway routing.

Configure the client with:

- `endpoint`: the Nexus Dashboard IP address or hostname.
- `auth`: your Nexus Dashboard username, password, and login domain.
- `nexus_dashboard_cluster_name`: the ACI/APIC cluster name as displayed in
  Nexus Dashboard.

This example requires your own Nexus Dashboard endpoint and account:

```python
from aciClient import AciClient, NexusDashboardPasswordAuth

with AciClient(
    endpoint="nd.example.test",
    auth=NexusDashboardPasswordAuth("admin", "secret", domain="local"),
    nexus_dashboard_cluster_name="production-fabric",  # ACI name in Nexus Dashboard
) as client:
    tenant = client.get_mo("uni/tn-aciclient").one()
    print(tenant.attributes["name"])
```

### Gateway limitations

- The ACI cluster must be registered in Nexus Dashboard and APIC Proxy Mode
  must be enabled. Cisco requires a Super-Admin account for the API Broker;
  the proxy mode determines the APIC identity used for requests. See
  [Cisco's API Broker configuration](https://www.cisco.com/c/en/us/td/docs/dcn/nd/4x/articles-411/templates-overview-and-operations.html).
- Gateway mode supports `NexusDashboardPasswordAuth` only. Direct APIC
  `PasswordAuth`, `CertificateAuth`, and Nexus Dashboard API keys are not
  supported in this mode. The login domain defaults to `local`.
- APIC subscriptions and WebSocket notifications are not supported through
  the gateway client.
- Each client targets one ACI cluster. Create a separate client for each
  additional cluster.
- ND login accepts `jwttoken` or `token`. Sessions refresh through the ND
  authentication API using `AuthCookie`; returned lifetimes or JWT expiry
  schedule refresh. JWT expiry is a scheduling hint. Expired sessions log in
  before the next request; rejected application requests are never replayed.
- ND login, refresh, and logout use `/api/v1/infra`. `logout()` validates
  the server response and always clears local state.
- APIC query parameters travel inside the proxy's `apicPath`. There has been
  two documented failures, wildcard anchors `^` (start of string) and `$` (end of
  string) are unsupported through ND → ACI but are supported directly on ACI. 

## Client lifecycle

Construction performs no network traffic. Login is lazy and coordinated when
multiple threads issue their first requests concurrently.

One client is both one APIC authentication-session boundary and the owner of a
bounded HTTP keep-alive pool. Several HTTP connections reuse the same token;
opening a connection does not log in again. Share one `AciClient` per
connection identity across threads. Do not create a client per request, copy
clients, share sockets after `fork()`, or share clients across processes.
Create a separate client in each worker process.

Applications own any `dict[ConnectionIdentity, AciClient]` registry and must
include endpoint, principal, domain, authentication type, gateway cluster,
TLS, and proxy configuration in its key. They also own idle eviction,
credential rotation, and shutdown. The library has no hidden registry or
multi-device client pool.

`HttpConnectionLimits` controls the HTTP connection pool. Each request is sent
once; callers own retries, queuing, and metrics because only they know whether
repeating an operation is safe.

The examples use Cisco's shared
[Always-On ACI Simulator](https://developer.cisco.com/docs/aci/sandbox/) and
its public credentials. The read example expects tenant `aciclient` to exist.

### Framework-neutral lifecycle

`serve_application()` represents your application's entry point. Its handlers
reuse the client passed to it, and the application closes the client during
shutdown.

```python
from aciClient import AciClient, PasswordAuth

client = AciClient(
    "sandboxapicdc.cisco.com", PasswordAuth("admin", "!v3G@!4@Y")
)
try:
    serve_application(client)       # handlers reuse this instance
finally:
    client.close()
```

### FastAPI lifecycle

Create one client per worker process using the application's lifespan. This
example requires FastAPI in your application environment. The synchronous
route reads tenant `aciclient` through the shared client.

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from aciClient import AciClient, PasswordAuth


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = AciClient(
        "sandboxapicdc.cisco.com", PasswordAuth("admin", "!v3G@!4@Y")
    )
    app.state.aci = client
    try:
        yield
    finally:
        client.close()


app = FastAPI(lifespan=lifespan)


@app.get("/tenant")
def get_tenant(request: Request):
    result = request.app.state.aci.get_mo("uni/tn-aciclient")
    return result.to_dict()
```

## Subscriptions

[subscription.py](subscription.py) subscribes to tenant `aciclient` and its
direct children. Run the [create example](#create-a-bridge-domain-in-a-tenant)
first. Subscriptions require direct APIC `PasswordAuth`; gateway and
certificate authentication are not supported.

**WebSocket setup, message reading, and refresh scheduling are application
code, outside aciClient.** The script uses
[`websockets.asyncio.client`](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html).
With aciClient installed, run from the repository root:

```bash
pip install websockets
python examples/subscription.py
```

The script logs in, reads `client.session_token`, opens the same session's
`wss://.../socket<TOKEN>` connection, then creates the subscription. See
[Cisco's subscription workflow](https://www.cisco.com/c/en/us/td/docs/dcn/aci/apic/all/apic-rest-api-configuration-guide/cisco-apic-rest-api-configuration-guide/m_using_the_rest_api.html)
for the protocol. `asyncio.to_thread()` keeps synchronous client HTTP calls
from blocking the WebSocket reader.

The application refreshes the subscription every 60 seconds for a 120-second
lifetime. WebSocket ping/pong does not replace this APIC refresh. When the
reader exits, the refresh loop stops and the WebSocket and client connections
close. Reconnection and recreating subscriptions remain application
responsibilities.

## Snapshots

Create a snapshot and wait for it to finish:

```python
job = client.create_snapshot(description="Before a configuration change")
result = job.wait(timeout=120)
print(result.snapshot_name)
```

`create_snapshot()` returns after submitting the export. `wait()` polls the
actual APIC run and returns `SnapshotResult` on success. It raises
`JobFailedError` if APIC reports failure or `JobTimeoutError` if the wait
expires. Timeouts retain the last response without additional discovery or
fault queries. Successful exports with warnings expose `result.warnings`.

The wait budget includes authentication and polling requests. No new poll is
sent once the budget expires, and a response that arrives after it cannot
report success. HTTP phase timeouts are capped as described for pagination;
a synchronous request cannot be forcibly interrupted at the exact deadline.

Secure fields are excluded by default. Pass `target_dn` to export a subtree,
`name` to choose the export policy name, or `include_secure_fields=True` to
include secure fields.

## Testing and observability

[`test_automation.py`](test_automation.py) demonstrates an automation function
with a reusable pytest fixture and scripted APIC responses. With aciClient and
pytest installed, run it from the repository root:

```bash
python -m pytest -q examples/test_automation.py
```

All requests in this example stay offline. The fixture closes the client and
checks that every expected request was made.

Inject a standard httpx transport. `aciClient.testing` provides
`ScriptedTransport`, request matchers, response builders, and `ManualClock`.
Unexpected calls fail without falling through to the network, repeated query
parameters are retained, and recorded credentials are redacted. The client
closes an injected transport when it is closed. See [TESTING.md](../TESTING.md)
for pytest examples.

`AciApiError` exposes structured APIC status, code, text, and redacted
response data. Authentication and transport failures have distinct exception
types. Invalid JSON, malformed managed objects, and invalid login, domain, or
subscription responses raise `ResponseDecodeError` instead of built-in parsing
exceptions.

## Legacy migration

`ACI`, `ACICert`, their historical import paths, constructor spellings
(`apicPasword`), attributes, methods, and return shapes remain in 2.0.
Construction emits one `LegacyAPIWarning`; `allow_legacy=True` is the 2.0
default. The 2.1 release targeted for October 2027 will require explicit
`allow_legacy=True`. Removal may be considered separately for 3.0, as may
removing insecure TLS support.

Replace calls incrementally:

- `getJson()` → `get_imdata()` or `get()`
- `postJson()` → `post_mo()`
- `deleteMo()` → `delete_mo()`
- `snapshot()` → `create_snapshot()` followed by `wait()`
