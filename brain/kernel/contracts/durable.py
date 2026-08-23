"""DurableExecution — reliable task lifecycle behind a stable port.

Retries, timers, crash recovery, replay. This is real but heavy, so it is
deferred: day-one implementation is plain Postgres state. When durability
genuinely earns its keep, slot Temporal (MIT — zero license asterisks)
behind this same interface. Inngest is lighter to operate but its core
server is SSPL; if ever used, stay on Cloud + Apache SDKs.

Defining the port now (and NOT pulling in an engine yet) is the lean call:
the interface is stable; the implementation swaps later.

SKELETON — signatures only, no implementation yet.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable


class DurableExecution(ABC):
    """Run work reliably. Survives crashes; retries with ceilings."""

    @abstractmethod
    async def submit(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> str:
        """Enqueue durable work. Returns a run id."""

    @abstractmethod
    async def status(self, run_id: str) -> Any:
        """Inspect a run."""

    @abstractmethod
    async def cancel(self, run_id: str) -> None:
        """Stop a run (and respect the hard cost/retry ceilings that bound it)."""
