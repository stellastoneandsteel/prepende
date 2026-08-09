"""http_api — reach the brain over HTTP from another machine or agent. Stdlib only.

    GET  /health            -> {ok, model}
    POST /goal  {"goal":..} -> {result, artifact, model}   (runs the Goal Loop)
    GET  /memory?q=...      -> {memories: [...]}            (recall)

This is the pragmatic "remote control": real remote access with zero dependencies.
The STANDARD agent-interop layer — an MCP server so OpenClaw and any MCP client
connect — is the next layer; it needs the `mcp` SDK (pip install mcp), so it lands
on a machine where that's available. Same brain behind both.

Auth: if PREPENDE_API_TOKEN is set, requests must send `Authorization: Bearer <token>`.
Binds to 127.0.0.1 by default — expose to a network deliberately, with a token.
Run:  python -m interface.http_api
"""

from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from kernel.core.brain import build_brain
from prepende_brain.env import brand_env

_loop = None
_cfg = None


def _brain():
    global _loop, _cfg
    if _loop is None:
        _loop, _cfg, _gw = build_brain()
    return _loop


def _run_goal(text: str) -> dict:
    loop = _brain()
    out: list[str] = []
    box = {"artifact": None, "error": None}

    async def on_event(ev: dict) -> None:
        if ev["type"] == "token":
            out.append(ev["text"])
        elif ev["type"] == "artifact":
            box["artifact"] = ev["text"]
        elif ev["type"] == "error":
            box["error"] = ev["text"]

    asyncio.run(loop.run(text, on_event))
    return {"result": "".join(out).strip(), "artifact": box["artifact"], "error": box["error"],
            "model": getattr(loop.gateway, "name", "?")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _auth_ok(self) -> bool:
        token = brand_env("API_TOKEN")
        return (not token) or self.headers.get("Authorization", "") == f"Bearer {token}"

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path)
        if path.path == "/health":
            return self._send(200, {"ok": True, "model": getattr(_brain().gateway, "name", "?")})
        if not self._auth_ok():
            return self._send(401, {"error": "unauthorized"})
        if path.path == "/memory":
            q = parse_qs(path.query).get("q", [""])[0]
            mem = _brain().memory
            hits = asyncio.run(mem.search(q, scope=_cfg.memory_scope, k=10)) if mem else []
            return self._send(200, {"memories": [h["content"] for h in hits]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._auth_ok():
            return self._send(401, {"error": "unauthorized"})
        if urlparse(self.path).path != "/goal":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("content-length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception as exc:
            return self._send(400, {"error": f"bad json: {exc}"})
        goal = (data.get("goal") or "").strip()
        if not goal:
            return self._send(400, {"error": "missing 'goal'"})
        try:
            return self._send(200, _run_goal(goal))
        except Exception as exc:
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})


def serve(host: str = "127.0.0.1", port: int | None = None) -> None:
    port = port or int(brand_env("HTTP_PORT", "8088"))
    _brain()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"Prepende HTTP API on http://{host}:{port}  ·  POST /goal · GET /memory · GET /health")
    print("auth: " + ("bearer token required" if brand_env("API_TOKEN") else "OPEN (set PREPENDE_API_TOKEN to require a token)"))
    srv.serve_forever()


if __name__ == "__main__":
    serve()
