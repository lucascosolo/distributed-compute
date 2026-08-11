"""Small, authenticated model-list probes for compatible provider APIs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib import error, request
from urllib.parse import urlsplit, urlunsplit
from typing import Callable


MAX_RESPONSE_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class ModelDiscovery:
    success: bool
    models: tuple[str, ...] = ()
    endpoint: str = ""
    error: str | None = None


def classify_model(model_id: str) -> dict[str, object]:
    """Conservative, explainable metadata for a live model ID.

    This is a routing hint, not benchmark evidence. Unknown names stay medium
    with low confidence and remain quarantined.
    """
    name = model_id.casefold()
    very_strong = any(token in name for token in ("120b", "405b", "pro", "r1", "reasoning"))
    strong = very_strong or any(token in name for token in ("70b", "72b", "32b", "30b", "coder", "large", "max"))
    light = any(token in name for token in ("nano", "mini", "lite", "1b", "3b", "7b", "8b", "small"))
    if very_strong:
        power, quota_weight = "very-strong", 2.0
    elif strong:
        power, quota_weight = "strong", 1.0
    elif light:
        power, quota_weight = "light", 0.5
    else:
        power, quota_weight = "medium", 1.0
    capabilities = ["classification", "extraction", "summarization"]
    if any(token in name for token in ("coder", "code", "starcoder")):
        capabilities.extend(("coding", "instruction_following"))
    if any(token in name for token in ("r1", "reason", "thinking", "o1", "o3")):
        capabilities.append("reasoning")
    confidence = "medium" if re.search(r"\d+[bm]", name) else "low"
    return {
        "id": model_id, "power": power, "quota_weight": quota_weight,
        "capabilities": capabilities, "metadata_confidence": confidence,
    }


def models_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("model discovery endpoint must be an absolute URL")
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if not path.endswith("/models"):
        path += "/models"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def discover_models(
    endpoint: str,
    api_key: str,
    *,
    api_key_optional: bool = False,
    timeout_seconds: float = 8.0,
    opener: Callable[..., object] = request.urlopen,
) -> ModelDiscovery:
    """Fetch a provider model list without exposing credentials or response bodies."""
    if not api_key.strip() and not api_key_optional:
        return ModelDiscovery(False, error="api_key_not_configured")
    url = models_endpoint(endpoint)
    headers = {"User-Agent": "aipool/0.1"}
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key}"
    req = request.Request(url, headers=headers, method="GET")
    try:
        with opener(req, timeout=timeout_seconds) as response:  # type: ignore[attr-defined]
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            return ModelDiscovery(False, endpoint=url, error="response_too_large")
        payload = json.loads(raw)
        values = payload.get("data", payload.get("models", [])) if isinstance(payload, dict) else payload
        if not isinstance(values, list):
            return ModelDiscovery(False, endpoint=url, error="model_list_not_found")
        models: list[str] = []
        for item in values[:512]:
            model = item.get("id") if isinstance(item, dict) else item
            if isinstance(model, str) and model.strip() and len(model) <= 256 and model not in models:
                models.append(model)
        return ModelDiscovery(True, tuple(models), url)
    except error.HTTPError as exc:
        return ModelDiscovery(False, endpoint=url, error=f"http_{exc.code}")
    except (error.URLError, TimeoutError):
        return ModelDiscovery(False, endpoint=url, error="unavailable")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ModelDiscovery(False, endpoint=url, error="invalid_model_list")
