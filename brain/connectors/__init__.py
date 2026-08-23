"""Connector hub — implementation of kernel.contracts.Connectors (outbound interop).

The brain's hands and senses. Speaks MCP as a CLIENT so any MCP-compliant
tool/server is reachable with no bespoke glue; non-MCP systems (and the owner's
own products — your own tools and services, etc.) get thin adapters behind the same port.

Pairs with interface/ (the inbound MCP server). Together they make the brain
bidirectional: others connect in, and the brain reaches out to everything.

Example connectors:
  - the owner's products: your own tools and services (thin adapters)
  - any MCP-compliant tool/server (no bespoke glue)
  - n8n (workflow automation) via its MCP/API — see note below.

n8n: a CONNECTOR, not the kernel. The Goal Loop (ours) does adaptive reasoning;
n8n handles deterministic, scheduled, multi-service automations the brain hands
off to and reads back. Bonus: n8n is itself a connector multiplexer (400+ apps),
so reaching it extends the brain's reach without hand-writing each adapter.
LICENSE NOTE: n8n is fair-code (Sustainable Use License), NOT OSI open source.
Calling a running n8n as an external service, and self-hosting it for our own
internal use, imposes nothing on our code and is allowed. We just never EMBED
n8n's source in the product or resell hosted-n8n. Safe as a connector.

SKELETON — Phase 3 (alongside the inbound MCP server).
"""
