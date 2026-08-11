"""Build conservative, non-active adapters for operator-approved discoveries."""

from __future__ import annotations

import json
from collections.abc import Mapping

from .domain import ProviderProfile, ProviderState
from .providers import HuggingFaceInferenceAdapter, OpenAICompatibleAdapter, ProviderAdapter


def build_discovered_adapter(
    row: Mapping[str, object],
    *,
    api_key_env: str,
    request_limit: int = 0,
    token_limit: int = 0,
    usage_window_seconds: float = 60.0,
) -> ProviderAdapter:
    """Create a quarantined adapter for one bounded smoke test.

    Discovery heuristics never grant complex-task routing capability. A later
    explicit activation workflow may use empirical benchmark evidence to make a
    separate decision.
    """
    model_key = str(row["model_key"])
    provider_slug = str(row["provider_slug"])
    transport = str(row["transport"])
    model_id = str(row["model_id"])
    endpoint = str(row["endpoint"])
    try:
        capabilities = tuple(str(item) for item in json.loads(str(row["capabilities_json"])))
    except (TypeError, ValueError, json.JSONDecodeError):
        capabilities = ()
    score = 0.6
    profile = ProviderProfile(
        f"discovered:{model_key}", f"Discovered {model_id}", transport,
        capabilities={capability: score for capability in capabilities if capability},
        reliability=0.2, state=ProviderState.QUARANTINED, max_complexity=2,
        quota_weight=float(row["quota_weight"]), quota_group=f"catalog:{provider_slug}",
        request_limit=request_limit, token_limit=token_limit,
        usage_window_seconds=usage_window_seconds,
    )
    if transport == "huggingface-api":
        return HuggingFaceInferenceAdapter(profile, model_id, api_key_env, endpoint)
    if transport == "openai-compatible":
        if not endpoint.rstrip("/").endswith("/chat/completions"):
            endpoint = endpoint.rstrip("/") + "/chat/completions"
        return OpenAICompatibleAdapter(profile, endpoint, model_id, api_key_env)
    raise ValueError("discovered transport does not support API smoke tests")
