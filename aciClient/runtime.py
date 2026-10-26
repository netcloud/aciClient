from __future__ import annotations

import time
from typing import Protocol


class SyncRuntime(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class SystemSyncRuntime:
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)
