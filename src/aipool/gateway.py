"""Bounded JSON HTTP gateway for local or explicitly authorized remote use."""

from __future__ import annotations

import json
import os
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from html import escape

from .domain import TaskEnvelope
from .queue import QueueFull, TaskQueue, record_to_dict
from .service import Coordinator


MAX_BODY_BYTES = 256 * 1024
CONFIG_KEYS = frozenset({
    "AIPOOL_HF_MODEL", "AIPOOL_HF_ENDPOINT", "AIPOOL_OPENAI_ENDPOINT",
    "AIPOOL_OPENAI_MODEL", "AIPOOL_BROWSER_COMMAND", "HF_TOKEN",
    "AIPOOL_OPENAI_API_KEY", "AIPOOL_TOKEN",
})
SECRET_KEYS = frozenset({"HF_TOKEN", "AIPOOL_OPENAI_API_KEY", "AIPOOL_TOKEN"})


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
    config_path: str | Path | None = None,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"} and not token:
        raise ValueError("a token is required for non-loopback gateway binding")

    task_queue = queue or TaskQueue(coordinator.store, max_pending=max_pending)
    operator_config = Path(config_path or os.environ.get("AIPOOL_CONFIG_FILE", ".aipool.local")).expanduser()

    def config_snapshot() -> dict[str, object]:
        file_values: dict[str, str] = {}
        if operator_config.is_file():
            for line in operator_config.read_text().splitlines():
                key, separator, value = line.partition("=")
                if separator and key in CONFIG_KEYS and value:
                    file_values[key] = value
        return {
            "settings": {
                key: os.environ.get(key, file_values.get(key, "")) for key in sorted(CONFIG_KEYS - SECRET_KEYS)
            },
            "secrets": {key: bool(os.environ.get(key) or file_values.get(key)) for key in sorted(SECRET_KEYS)},
            "config_path": str(operator_config),
            "restart_required": True,
        }

    def save_config(updates: dict[str, str]) -> None:
        operator_config.parent.mkdir(parents=True, exist_ok=True)
        existing = operator_config.read_text() if operator_config.exists() else ""
        lines = existing.splitlines()
        seen: set[str] = set()
        rendered: list[str] = []
        for line in lines:
            key, separator, _ = line.partition("=")
            if separator and key in updates:
                rendered.append(f"{key}={updates[key]}")
                seen.add(key)
            else:
                rendered.append(line)
        rendered.extend(f"{key}={updates[key]}" for key in sorted(updates) if key not in seen)
        payload = ("\n".join(rendered).rstrip("\n") + "\n").encode()
        fd, temporary = tempfile.mkstemp(prefix=".aipool.local.", dir=operator_config.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
            os.replace(temporary, operator_config)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        os.chmod(operator_config, 0o600)

    class Handler(BaseHTTPRequestHandler):
        server_version = "aipool/0.1"

        def _send(self, status: int, payload: dict[str, object]) -> None:
            encoded = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_html(self, status: int, body: str) -> None:
            encoded = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
            if self.path == "/admin/config":
                self._send(200, config_snapshot())
                return
            if self.path == "/admin":
                self._send_html(200, """<!doctype html><meta charset=utf-8>
<title>aipool provider configuration</title><h1>Provider configuration</h1>
<p>Secrets are never displayed. Changes are written to the operator config and require a restart.</p>
<form id=f><label>HF model <input name=AIPOOL_HF_MODEL></label><br>
<label>HF token <input name=HF_TOKEN type=password autocomplete=new-password></label><br>
<label>OpenAI-compatible endpoint <input name=AIPOOL_OPENAI_ENDPOINT></label><br>
<label>OpenAI model <input name=AIPOOL_OPENAI_MODEL></label><br>
<label>Authorized browser wrapper <input name=AIPOOL_BROWSER_COMMAND></label><br>
<label>OpenAI key <input name=AIPOOL_OPENAI_API_KEY type=password autocomplete=new-password></label><br>
<button>Save</button></form><pre id=o></pre>
<script>f.onsubmit=async e=>{e.preventDefault();let o={};for(let [k,v] of new FormData(f))if(v)o[k]=v;
let r=await fetch('/admin/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o)});
o=await r.json();document.querySelector('#o').textContent=JSON.stringify(o,null,2);f.reset()}</script>""")
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
            if path == "/admin/config":
                try:
                    payload = self._read_json()
                    if not isinstance(payload, dict):
                        raise ValueError("configuration must be an object")
                    updates = {
                        str(key): str(value) for key, value in payload.items()
                        if str(key) in CONFIG_KEYS and isinstance(value, str) and value
                    }
                    save_config(updates)
                except (ValueError, TypeError, KeyError, json.JSONDecodeError, OSError) as exc:
                    self._send(400, {"updated": False, "error": str(exc)[:300]})
                    return
                self._send(200, {"updated": True, "saved_keys": sorted(updates), "config_path": str(operator_config), "restart_required": True})
                return
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
    server.aipool_coordinator = coordinator  # type: ignore[attr-defined]
    return server
