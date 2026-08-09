"""Explicit multi-provider generation routing for Prepende.

Enabled only with ``MODEL_PROVIDER=auto``. That setting is the owner's consent
to route generation across the configured providers in
``PREPENDE_MODEL_ROUTE``. Normal provider settings remain provider-local.
"""

from __future__ import annotations

import contextvars
from typing import Any, AsyncIterator, Sequence

from kernel.contracts import ModelGateway


class AutoGateway(ModelGateway):
    """Try configured generation gateways in an explicit preference order."""

    name = "auto"
    auth_lane = "multi-provider"

    def __init__(self, gateways: Sequence[ModelGateway]) -> None:
        self.gateways = tuple(gateways)
        if not self.gateways:
            raise RuntimeError(
                "MODEL_PROVIDER=auto requires at least one configured API provider"
            )
        self.requested_model = self._label(self.gateways[0], resolved=False)
        self.model = self.requested_model
        self._last_resolution: tuple[str, str] | None = None
        self._resolution: contextvars.ContextVar[tuple[str, str] | None] = (
            contextvars.ContextVar(
                f"prepende_auto_resolution_{id(self)}",
                default=None,
            )
        )

    @staticmethod
    def _provider(gateway: ModelGateway) -> str:
        return str(
            getattr(gateway, "name", gateway.__class__.__name__) or "unknown"
        ).lower()

    @classmethod
    def _label(cls, gateway: ModelGateway, *, resolved: bool) -> str:
        model = getattr(gateway, "resolved_model", None) if resolved else None
        model = (
            model
            or getattr(gateway, "requested_model", None)
            or getattr(gateway, "model", None)
            or "provider-default"
        )
        return f"{cls._provider(gateway)}:{model}"

    @property
    def resolved_provider(self) -> str | None:
        value = self._resolution.get() or self._last_resolution
        return value[0] if value else None

    @property
    def resolved_model(self) -> str | None:
        value = self._resolution.get() or self._last_resolution
        return value[1] if value else None

    def _record(self, gateway: ModelGateway) -> None:
        value = (self._provider(gateway), self._label(gateway, resolved=True))
        self._last_resolution = value
        self._resolution.set(value)

    @staticmethod
    def _routeable(exc: Exception) -> bool:
        # Invalid local arguments and contract failures should not be disguised
        # by another provider. Provider HTTP/auth/billing/quota/network errors
        # are routeable because MODEL_PROVIDER=auto explicitly opted in.
        return not isinstance(exc, (AssertionError, TypeError, ValueError))

    async def complete(
        self,
        messages: Sequence[dict[str, Any]],
        **opts: Any,
    ) -> Any:
        last_error: Exception | None = None
        for index, gateway in enumerate(self.gateways):
            try:
                answer = await gateway.complete(messages, **opts)
                self._record(gateway)
                return answer
            except Exception as exc:
                last_error = exc
                if not self._routeable(exc) or index == len(self.gateways) - 1:
                    raise
        raise last_error or RuntimeError("automatic model routing failed")

    async def stream(
        self,
        messages: Sequence[dict[str, Any]],
        **opts: Any,
    ) -> AsyncIterator[Any]:
        # Resolve before yielding. Retrying after a partial response could splice
        # two providers into one answer and make provenance dishonest.
        text = await self.complete(messages, **opts)
        for word in str(text).split(" "):
            yield word + " "

    async def embed(
        self,
        texts: Sequence[str],
        **opts: Any,
    ) -> Sequence[Sequence[float]]:
        raise NotImplementedError(
            "MODEL_PROVIDER=auto routes generation only; configure the embedding "
            "provider explicitly so one index never mixes vector spaces"
        )
