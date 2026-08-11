"""Bounded JSON HTTP gateway for local or explicitly authorized remote use."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .domain import TaskEnvelope
from .queue import QueueFull, TaskQueue, record_to_dict
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


def make_server(
    coordinator: Coordinator,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str | None = None,
    queue: TaskQueue | None = None,
    max_pending: int = 1000,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"} and not token:
        raise ValueError("a token is required for non-loopback gateway binding")

    task_queue = queue or TaskQueue(coordinator.store, max_pending=max_pending)

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

        def _read_json(self) -> object:
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 0 or length > MAX_BODY_BYTES:
                raise ValueError("request body exceeds limit")
            return json.loads(self.rfile.read(length))

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
            path = urlsplit(self.path).path
            if path.startswith("/queue/"):
                task_id = path.removeprefix("/queue/")
                if not task_id or "/" in task_id:
                    self._send(404, {"error": "not_found"})
                    return
                record = task_queue.get(task_id)
                if record is None:
                    self._send(404, {"error": "not_found"})
                    return
                self._send(200, record_to_dict(record))
                return
            self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            path = urlsplit(self.path).path
            if path == "/queue":
                try:
                    payload = self._read_json()
                    if not isinstance(payload, dict):
                        raise ValueError("request body must be an object")
                    task_payload = payload.get("task", payload)
                    if not isinstance(task_payload, dict):
                        raise ValueError("task must be an object")
                    task = TaskEnvelope.from_dict(task_payload)
                    idempotency_key = self.headers.get("Idempotency-Key") or (
                        str(payload["idempotency_key"]) if "idempotency_key" in payload else None
                    )
                    record = task_queue.enqueue(task, idempotency_key=idempotency_key)
                except QueueFull as exc:
                    self._send(429, {"error": str(exc)})
                    return
                except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                    self._send(400, {"error": str(exc)[:300]})
                    return
                self._send(202, record_to_dict(record))
                return
            if path.endswith("/cancel") and path.startswith("/queue/"):
                task_id = path[len("/queue/"):-len("/cancel")].strip("/")
                if not task_id or "/" in task_id:
                    self._send(404, {"error": "not_found"})
                    return
                record = task_queue.get(task_id)
                if record is None:
                    self._send(404, {"error": "not_found"})
                    return
                task_queue.cancel(task_id)
                self._send(200, record_to_dict(task_queue.get(task_id)))
                return
            if path != "/task":
                self._send(404, {"error": "not_found"})
                return
            try:
                payload = self._read_json()
                if not isinstance(payload, dict):
                    raise ValueError("request body must be an object")
                outcome = coordinator.submit(TaskEnvelope.from_dict(payload))
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                self._send(400, {"error": str(exc)[:300]})
                return
            self._send(200, _outcome_json(outcome))

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.aipool_queue = task_queue  # type: ignore[attr-defined]
    return server
