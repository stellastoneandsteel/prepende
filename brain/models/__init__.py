"""Swappable model providers — implementations of kernel.contracts.ModelGateway.

VENDOR-NEUTRAL. No provider is privileged; use whatever AI you want:
  - anthropic           (Claude)
  - openai              (GPT)
  - google              (Gemini)
  - openai-compatible   (OpenRouter / Together / Groq / Fireworks / vLLM — any
                         OpenAI-API-speaking endpoint, incl. open-weight models)
  - local               (Ollama / vLLM / llama.cpp on your own hardware)

This is the only place a model SDK is imported. Adding a provider = a new
adapter here + a config value; no caller changes.

Phase 0 builds AT LEAST TWO adapters from different vendors (one of them
open-weight or local) — proving neutrality is real, not a default allegiance.

SKELETON.
"""
