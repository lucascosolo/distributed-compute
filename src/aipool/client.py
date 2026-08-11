"""Small authenticated HTTP client for a remote aipool gateway."""

from __future__ import annotations

import json
from typing import Callable, Mapping
from urllib import error, request

from .domain import TaskEnvelope


class RemoteCoordinatorError(RuntimeError):
    """A remote coordinator could not accept or return a task."""


def _remote_json(
    base_url: str,
    path: str,
    *,
    token: str | None,
    method: str,
    payload: object | None = None,
    headers_extra: Mapping[str, str] | None = None,
    timeout_seconds: float,
    opener: Callable[..., object],
) -> Mapping[str, object]:
    base_url = base_url.strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise RemoteCoordinatorError("remote coordinator URL must use http or https")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if headers_extra:
        headers.update(headers_extra)
    body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    req = request.Request(f"{base_url}{path}", data=body, headers=headers, method=method)
    try:
        with opener(req, timeout=timeout_seconds) as response:  # type: ignore[attr-defined]
            response_payload = json.loads(response.read())
    except error.HTTPError as exc:
        raise RemoteCoordinatorError(f"remote coordinator returned HTTP {exc.code}") from exc
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError, TypeError) as exc:
        raise RemoteCoordinatorError(f"remote coordinator request failed: {exc}") from exc
    if not isinstance(response_payload, dict):
        raise RemoteCoordinatorError("remote coordinator returned a non-object response")
    return response_payload


def submit_remote(
    base_url: str,
    task: TaskEnvelope,
    *,
    token: str | None,
    headers_extra: Mapping[str, str] | None = None,
    timeout_seconds: float = 30.0,
    opener: Callable[..., object] = request.urlopen,
) -> Mapping[str, object]:
    return _remote_json(
        base_url, "/task", token=token, method="POST", payload=task.to_dict(),
        headers_extra=headers_extra, timeout_seconds=timeout_seconds, opener=opener,
    )


def enqueue_remote(
    base_url: str,
    task: TaskEnvelope,
    *,
    token: str | None,
    headers_extra: Mapping[str, str] | None = None,
    idempotency_key: str | None = None,
    timeout_seconds: float = 30.0,
    opener: Callable[..., object] = request.urlopen,
) -> Mapping[str, object]:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    return _remote_json(
        base_url, "/queue", token=token, method="POST", payload=task.to_dict(),
        headers_extra={**(headers_extra or {}), **(headers or {})},
        timeout_seconds=timeout_seconds, opener=opener,
    )


def get_remote_queue(
    base_url: str,
    task_id: str,
    *,
    token: str | None,
    headers_extra: Mapping[str, str] | None = None,
    timeout_seconds: float = 30.0,
    opener: Callable[..., object] = request.urlopen,
) -> Mapping[str, object]:
    return _remote_json(
        base_url, f"/queue/{task_id}", token=token, method="GET",
        headers_extra=headers_extra, timeout_seconds=timeout_seconds, opener=opener,
    )


def cancel_remote(
    base_url: str,
    task_id: str,
    *,
    token: str | None,
    headers_extra: Mapping[str, str] | None = None,
    timeout_seconds: float = 30.0,
    opener: Callable[..., object] = request.urlopen,
) -> Mapping[str, object]:
    return _remote_json(
        base_url, f"/queue/{task_id}/cancel", token=token, method="POST", payload={},
        headers_extra=headers_extra, timeout_seconds=timeout_seconds, opener=opener,
    )
