"""APIC subscription handles."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .auth import Auth, PasswordAuth
from .exceptions import ConfigurationError
from .models import AciResponse, SubscriptionInitialResponse

if TYPE_CHECKING:
    from .client import AciClient


def validate_subscription_auth(auth: Auth, cluster_name: str | None) -> None:
    if cluster_name or not isinstance(auth, PasswordAuth):
        raise ConfigurationError("subscriptions require direct APIC PasswordAuth")


class Subscription:
    def __init__(
        self,
        client: AciClient,
        initial_response: SubscriptionInitialResponse,
        refresh_timeout: int | None,
    ) -> None:
        self._client = client
        self.initial_response = initial_response
        self.refresh_timeout = refresh_timeout

    @property
    def subscription_id(self) -> str:
        return self.initial_response.subscription_id

    def refresh(self) -> AciResponse:
        return self._client.get(
            "subscriptionRefresh.json", params={"id": self.subscription_id}
        )
