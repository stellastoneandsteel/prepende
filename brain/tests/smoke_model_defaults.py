"""Offline smoke for every canonical OpenAI and Anthropic model route."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import models.anthropic as anthropic_module  # noqa: E402
import models.openai as openai_module  # noqa: E402
from models.anthropic import AnthropicGateway  # noqa: E402
from models.catalog import (  # noqa: E402
    ANTHROPIC_FABLE,
    ANTHROPIC_OPUS_48,
    ANTHROPIC_SONNET_5,
    OPENAI_LUNA,
    OPENAI_LEGACY,
    OPENAI_SOL,
    OPENAI_TERRA,
    model_fallbacks,
    resolve_model_id,
)
from models.factory import build_gateway, build_gateway_from  # noqa: E402
from models.openai import OpenAIGateway  # noqa: E402
from models.provenance import model_provenance  # noqa: E402
from kernel.core.config import Config  # noqa: E402


def main() -> None:
    assert resolve_model_id("openai", "sol") == OPENAI_SOL
    assert resolve_model_id("openai", "terra") == OPENAI_TERRA
    assert resolve_model_id("openai", "luna") == OPENAI_LUNA
    assert resolve_model_id("anthropic", "fable") == ANTHROPIC_FABLE
    assert resolve_model_id("anthropic", "opus-4.8") == ANTHROPIC_OPUS_48
    assert resolve_model_id("anthropic", "sonnet-5") == ANTHROPIC_SONNET_5
    assert resolve_model_id("cli-claude", "fable") == ANTHROPIC_FABLE
    assert resolve_model_id("cli-claude", "opus-4-8") == ANTHROPIC_OPUS_48
    assert resolve_model_id("cli-codex", "terra") == OPENAI_TERRA
    assert resolve_model_id("openai", "gpt-custom") == "gpt-custom"
    assert model_fallbacks("openai", OPENAI_SOL) == (OPENAI_TERRA, OPENAI_LUNA, OPENAI_LEGACY)
    assert model_fallbacks("anthropic", ANTHROPIC_FABLE) == (
        ANTHROPIC_OPUS_48,
        ANTHROPIC_SONNET_5,
    )
    assert model_fallbacks("anthropic", ANTHROPIC_OPUS_48) == (
        ANTHROPIC_SONNET_5,
    )

    os.environ["OPENAI_API_KEY"] = "test-openai-key"
    os.environ["ANTHROPIC_API_KEY"] = "test-anthropic-key"
    os.environ["MODEL_PROVIDER"] = "openai"
    # A whitespace value is truthy to load_dotenv (so a developer's local
    # .env pin cannot leak into this test) and normalizes to the intended blank.
    os.environ["MODEL_NAME"] = " "
    cfg = Config()
    assert build_gateway(cfg).model == OPENAI_SOL
    assert build_gateway(cfg, model="terra").model == OPENAI_TERRA
    assert build_gateway_from("openai", model="luna", key="test-key").model == OPENAI_LUNA

    os.environ["MODEL_PROVIDER"] = "anthropic"
    cfg = Config()
    assert build_gateway(cfg).model == ANTHROPIC_FABLE
    assert build_gateway(cfg, model="fable").model == ANTHROPIC_FABLE
    assert build_gateway_from("anthropic", model="fable", key="test-key").model == ANTHROPIC_FABLE

    # Every configured current model works through both its hosted API adapter
    # and its local subscription CLI adapter. These are provider-local lanes;
    # an Anthropic ID must never be sent to OpenAI or vice versa.
    for model in (OPENAI_SOL, OPENAI_TERRA, OPENAI_LUNA, OPENAI_LEGACY):
        assert build_gateway_from("openai", model=model, key="test-key").model == model
        codex_cli = build_gateway(cfg, provider="cli-codex", model=model)
        assert codex_cli.command[-2:] == ["-m", model], codex_cli.command
    for model in (
        ANTHROPIC_FABLE,
        ANTHROPIC_OPUS_48,
        ANTHROPIC_SONNET_5,
    ):
        assert build_gateway_from("anthropic", model=model, key="test-key").model == model
        claude_cli = build_gateway(cfg, provider="cli-claude", model=model)
        assert claude_cli.command[-2:] == ["--model", model], claude_cli.command

    assert build_gateway(cfg, provider="cli-claude", model="opus-4.8").command[-1] == ANTHROPIC_OPUS_48
    assert build_gateway(cfg, provider="cli-codex", model="terra").command[-1] == OPENAI_TERRA

    # Direct adapter defaults stay aligned with factory defaults.
    assert OpenAIGateway("test-key").model == OPENAI_SOL
    assert AnthropicGateway("test-key").model == ANTHROPIC_FABLE
    assert OpenAIGateway("test-key", "terra").model == OPENAI_TERRA
    assert AnthropicGateway("test-key", "fable").model == ANTHROPIC_FABLE

    try:
        resolve_model_id("openai", "fable")
    except ValueError as exc:
        assert "anthropic" in str(exc)
    else:
        raise AssertionError("cross-provider alias was accepted")

    # Availability fallback is bounded and provider-local: a rejected primary
    # model moves to the next configured ID and records the resolved model.
    openai_calls: list[dict[str, object]] = []
    old_openai_post = openai_module.post_json

    def fake_openai_post(url: str, payload: bytes, headers: dict[str, str]) -> bytes:
        body = json.loads(payload)
        openai_calls.append(body)
        if body["model"] == OPENAI_SOL:
            raise urllib.error.HTTPError(url, 404, "model unavailable", {}, None)
        return json.dumps({"choices": [{"message": {"content": "terra answer"}}]}).encode()

    try:
        openai_module.post_json = fake_openai_post
        gateway = OpenAIGateway("test-key", OPENAI_SOL, fallback_models=model_fallbacks("openai", OPENAI_SOL))
        assert asyncio.run(gateway.complete([{"role": "user", "content": "hello"}])) == "terra answer"
        assert [call["model"] for call in openai_calls] == [OPENAI_SOL, OPENAI_TERRA], openai_calls
        assert gateway.resolved_model == OPENAI_TERRA
        assert gateway.model == OPENAI_SOL, "fallback must not become sticky"
        assert asyncio.run(gateway.complete([{"role": "user", "content": "hello again"}])) == "terra answer"
        assert [call["model"] for call in openai_calls] == [
            OPENAI_SOL,
            OPENAI_TERRA,
            OPENAI_SOL,
            OPENAI_TERRA,
        ], openai_calls
    finally:
        openai_module.post_json = old_openai_post

    anthropic_calls: list[dict[str, object]] = []
    old_anthropic_post = anthropic_module.post_json

    def fake_anthropic_post(url: str, payload: bytes, headers: dict[str, str]) -> bytes:
        body = json.loads(payload)
        anthropic_calls.append(body)
        if body["model"] == ANTHROPIC_FABLE:
            error = urllib.error.HTTPError(url, 404, "model unavailable", {}, None)
            error.prepende_body = b'{"error":{"type":"not_found_error","message":"model not found"}}'
            raise error
        return json.dumps({"content": [{"text": "opus answer"}]}).encode()

    try:
        anthropic_module.post_json = fake_anthropic_post
        gateway = AnthropicGateway("test-key", ANTHROPIC_FABLE, model_fallbacks("anthropic", ANTHROPIC_FABLE))
        assert asyncio.run(gateway.complete([{"role": "user", "content": "hello"}])) == "opus answer"
        assert [call["model"] for call in anthropic_calls] == [ANTHROPIC_FABLE, ANTHROPIC_OPUS_48], anthropic_calls
        assert gateway.resolved_model == ANTHROPIC_OPUS_48
        assert gateway.model == ANTHROPIC_FABLE, "fallback must not become sticky"
    finally:
        anthropic_module.post_json = old_anthropic_post

    # A billing 400 is terminal. It must not walk the model chain and obscure
    # the owner-controlled credit blocker as a fake availability problem.
    billing_calls: list[dict[str, object]] = []

    def fake_anthropic_billing(url: str, payload: bytes, headers: dict[str, str]) -> bytes:
        billing_calls.append(json.loads(payload))
        error = urllib.error.HTTPError(url, 400, "bad request", {}, None)
        error.prepende_body = json.dumps({
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "Your credit balance is too low.",
            },
        }).encode()
        raise error

    try:
        anthropic_module.post_json = fake_anthropic_billing
        gateway = AnthropicGateway(
            "test-key",
            ANTHROPIC_FABLE,
            model_fallbacks("anthropic", ANTHROPIC_FABLE),
        )
        try:
            asyncio.run(gateway.complete([{"role": "user", "content": "hello"}]))
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("billing failure did not surface")
        assert [call["model"] for call in billing_calls] == [ANTHROPIC_FABLE], billing_calls
    finally:
        anthropic_module.post_json = old_anthropic_post

    # A generic OpenAI-compatible endpoint may use the same literal model name
    # but still require the legacy token field. First-party request semantics
    # must never leak into that adapter.
    compatible_calls: list[dict[str, object]] = []

    def fake_compatible_post(url: str, payload: bytes, headers: dict[str, str]) -> bytes:
        compatible_calls.append(json.loads(payload))
        return json.dumps({"choices": [{"message": {"content": "compatible answer"}}]}).encode()

    try:
        openai_module.post_json = fake_compatible_post
        gateway = OpenAIGateway(
            "",
            OPENAI_SOL,
            "http://compatible.invalid/v1",
            "openai-compatible",
        )
        assert asyncio.run(gateway.complete([{"role": "user", "content": "hello"}])) == "compatible answer"
        assert "max_tokens" in compatible_calls[0], compatible_calls
        assert "max_completion_tokens" not in compatible_calls[0], compatible_calls
    finally:
        openai_module.post_json = old_openai_post

    async def concurrent_resolution_check() -> None:
        gateway = OpenAIGateway(
            "test-key",
            OPENAI_SOL,
            fallback_models=(OPENAI_TERRA,),
        )

        def fake_concurrent_post(url: str, payload: bytes, headers: dict[str, str]) -> bytes:
            body = json.loads(payload)
            content = body["messages"][-1]["content"]
            if content == "needs fallback" and body["model"] == OPENAI_SOL:
                raise urllib.error.HTTPError(url, 404, "model unavailable", {}, None)
            return json.dumps({"choices": [{"message": {"content": content}}]}).encode()

        async def one(content: str) -> tuple[str, str | None]:
            answer = await gateway.complete([{"role": "user", "content": content}])
            return answer, model_provenance(gateway).resolved_model

        openai_module.post_json = fake_concurrent_post
        primary, fallback = await asyncio.gather(one("primary"), one("needs fallback"))
        assert primary == ("primary", None), primary
        assert fallback == ("needs fallback", OPENAI_TERRA), fallback

    try:
        asyncio.run(concurrent_resolution_check())
    finally:
        openai_module.post_json = old_openai_post

    print("MODEL DEFAULTS SMOKE: OK")
    print("  openai          : Sol/Terra/Luna/GPT-5.5 API + subscription routes")
    print("  anthropic       : Fable/Opus/Sonnet API + subscription routes")
    print("  overrides       : aliases and exact model IDs remain honored")


if __name__ == "__main__":
    main()
