"""Small authenticated HTTP client for a remote aipool gateway."""

from __future__ import annotations

import json
import base64
import socket
import time
from typing import Callable, Mapping
from urllib import error, request

from .domain import TaskEnvelope


class RemoteCoordinatorError(RuntimeError):
    """A remote coordinator could not accept or return a task."""


def _is_transient_dns_error(exc: error.URLError) -> bool:
    """Retry only resolver failures, before risking a duplicate POST."""
    return isinstance(exc.reason, socket.gaierror)


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
    # Some authorized edge proxies reject urllib's default Python user agent;
    # identify the coordinator client explicitly for remote gateway traffic.
    headers = {"Content-Type": "application/json", "User-Agent": "aipool/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if headers_extra:
        headers.update(headers_extra)
    body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    req = request.Request(f"{base_url}{path}", data=body, headers=headers, method=method)
    for attempt in range(3):
        try:
            with opener(req, timeout=timeout_seconds) as response:  # type: ignore[attr-defined]
                response_payload = json.loads(response.read())
            break
        except error.HTTPError as exc:
            raise RemoteCoordinatorError(f"remote coordinator returned HTTP {exc.code}") from exc
        except error.URLError as exc:
            if attempt == 2 or not _is_transient_dns_error(exc):
                raise RemoteCoordinatorError(f"remote coordinator request failed: {exc}") from exc
            time.sleep(0.5 * (2**attempt))
        except (TimeoutError, OSError, json.JSONDecodeError, TypeError) as exc:
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
    timeout_seconds: float = 180.0,
    opener: Callable[..., object] = request.urlopen,
) -> Mapping[str, object]:
    return _remote_json(
        base_url, "/task", token=token, method="POST", payload=task.to_dict(),
        headers_extra=headers_extra, timeout_seconds=timeout_seconds, opener=opener,
    )


def get_remote_capabilities(
    base_url: str,
    *,
    token: str | None,
    headers_extra: Mapping[str, str] | None = None,
    timeout_seconds: float = 30.0,
    opener: Callable[..., object] = request.urlopen,
) -> Mapping[str, object]:
    return _remote_json(
        base_url, "/capabilities", token=token, method="GET",
        headers_extra=headers_extra, timeout_seconds=timeout_seconds, opener=opener,
    )


def upload_artifact_remote(
    base_url: str,
    content: bytes,
    *,
    token: str | None,
    headers_extra: Mapping[str, str] | None = None,
    timeout_seconds: float = 180.0,
    opener: Callable[..., object] = request.urlopen,
) -> str:
    """Upload one bounded context artifact and return its content-addressed ref."""
    if len(content) > 128 * 1024:
        raise RemoteCoordinatorError("artifact exceeds 131072 byte limit")
    result = _remote_json(
        base_url, "/artifact", token=token, method="POST",
        payload={"content": base64.b64encode(content).decode("ascii")},
        headers_extra=headers_extra, timeout_seconds=timeout_seconds, opener=opener,
    )
    reference = result.get("reference")
    if not isinstance(reference, str) or not reference.startswith("artifact:sha256:"):
        raise RemoteCoordinatorError("remote coordinator returned an invalid artifact reference")
    return reference


def enqueue_remote(
    base_url: str,
    task: TaskEnvelope,
    *,
    token: str | None,
    headers_extra: Mapping[str, str] | None = None,
    idempotency_key: str | None = None,
    timeout_seconds: float = 180.0,
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
    timeout_seconds: float = 180.0,
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
    timeout_seconds: float = 180.0,
    opener: Callable[..., object] = request.urlopen,
) -> Mapping[str, object]:
    return _remote_json(
        base_url, f"/queue/{task_id}/cancel", token=token, method="POST", payload={},
        headers_extra=headers_extra, timeout_seconds=timeout_seconds, opener=opener,
    )
