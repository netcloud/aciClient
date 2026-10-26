"""Subscribe to tenant aciclient using application-owned WebSocket handling.

Install the optional example dependency with: pip install websockets
Create tenant aciclient with examples/README.md before running this script.
"""

import asyncio
import json

from websockets.asyncio.client import connect

from aciClient import AciClient, AciQuery, PasswordAuth


async def refresh_subscription(subscription, stopped):
    # Refresh scheduling is application code, outside aciClient.
    while not stopped.is_set():
        try:
            await asyncio.wait_for(stopped.wait(), timeout=60)
        except TimeoutError:
            await asyncio.to_thread(subscription.refresh)


async def main():
    with AciClient(
        "sandboxapicdc.cisco.com", PasswordAuth("admin", "!v3G@!4@Y")
    ) as client:
        await asyncio.to_thread(client.login)
        token = client.session_token
        assert token is not None

        # WebSocket setup and message handling are not part of aciClient.
        endpoint = client.connection_identity.endpoint.replace("https://", "wss://", 1)
        async with connect(f"{endpoint}/socket{token}") as websocket:
            subscription = await asyncio.to_thread(
                client.subscribe_mo,
                "uni/tn-aciclient",
                params=AciQuery(rsp_subtree="children"),
                refresh_timeout=120,
            )
            print("Subscription:", subscription.subscription_id)
            for tenant in subscription.initial_response.objects:
                print("Initial object:", tenant.dn)

            stopped = asyncio.Event()
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(refresh_subscription(subscription, stopped))
                try:
                    async for message in websocket:
                        event = json.loads(message)
                        print(event.get("subscriptionId"), event.get("imdata", []))
                finally:
                    stopped.set()


if __name__ == "__main__":
    asyncio.run(main())
