"""Small authenticated HTTP client for a remote aipool gateway."""

from __future__ import annotations

import json
from typing import Callable, Mapping
from urllib import error, request

from .domain import TaskEnvelope


class RemoteCoordinatorError(RuntimeError):
    """A remote coordinator could not accept or return a task."""


def submit_remote(
    base_url: str,
    task: TaskEnvelope,
    *,
    token: str | None,
    timeout_seconds: float = 30.0,
    opener: Callable[..., object] = request.urlopen,
) -> Mapping[str, object]:
    base_url = base_url.strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise RemoteCoordinatorError("remote coordinator URL must use http or https")
    body = json.dumps(task.to_dict(), separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(f"{base_url}/task", data=body, headers=headers, method="POST")
    try:
        with opener(req, timeout=timeout_seconds) as response:  # type: ignore[attr-defined]
            payload = json.loads(response.read())
    except error.HTTPError as exc:
        raise RemoteCoordinatorError(f"remote coordinator returned HTTP {exc.code}") from exc
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError, TypeError) as exc:
        raise RemoteCoordinatorError(f"remote coordinator request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RemoteCoordinatorError("remote coordinator returned a non-object response")
    return payload
