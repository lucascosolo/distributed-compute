"""Bounded JSON HTTP gateway for local or explicitly authorized remote use."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .domain import TaskEnvelope
from .service import Coordinator


MAX_BODY_BYTES = 256 * 1024


def _outcome_json(outcome) -> dict[str, object]:
    return {
        "task_id": outcome.task_id,
        "success": outcome.success,
        "valid": outcome.valid,
        "provider_id": outcome.provider_id,
        "output": outcome.output,
        "reason": outcome.reason,
        "orchestration_cost": outcome.orchestration_cost,
        "delegated_compute_saved": outcome.delegated_compute_saved,
        "worker_tokens": outcome.worker_tokens,
        "native_fallback": outcome.native_fallback,
        "next_action": "native_model" if outcome.native_fallback else "return_result",
    }


def make_server(coordinator: Coordinator, *, host: str = "127.0.0.1", port: int = 8765, token: str | None = None) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"} and not token:
        raise ValueError("a token is required for non-loopback gateway binding")

    class Handler(BaseHTTPRequestHandler):
        server_version = "aipool/0.1"

        def _send(self, status: int, payload: dict[str, object]) -> None:
            encoded = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _authorized(self) -> bool:
            return token is None or self.headers.get("Authorization") == f"Bearer {token}"

        def _operational_status(self) -> dict[str, object]:
            profiles = coordinator.health.profiles(adapter.profile for adapter in coordinator.registry.all())
            return {
                "providers": len(profiles),
                "provider_states": [
                    {"id": profile.id, "state": profile.state.value}
                    for profile in profiles
                ],
                "stats": coordinator.store.stats(),
            }

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            if self.path == "/status":
                self._send(200, self._operational_status())
                return
            if self.path in {"/stats", "/metrics"}:
                self._send(200, coordinator.store.stats())
                return
            self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            if self.path != "/task":
                self._send(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if length < 0 or length > MAX_BODY_BYTES:
                    raise ValueError("request body exceeds limit")
                payload = json.loads(self.rfile.read(length))
                outcome = coordinator.submit(TaskEnvelope.from_dict(payload))
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                self._send(400, {"error": str(exc)[:300]})
                return
            self._send(200, _outcome_json(outcome))

        def log_message(self, *_args) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)
