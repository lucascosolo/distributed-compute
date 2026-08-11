"""Validated, non-secret metadata for providers shown in the operator panel."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


CATALOG_PATH = Path(__file__).resolve().parents[2] / "providers" / "candidate-catalog.json"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class CatalogProvider:
    provider_slug: str
    provider_name: str
    slug: str
    name: str
    model: str
    power: str
    quota_weight: float
    endpoint: str
    source_url: str
    transport: str
    quota_status: str = "unknown"
    quota_scope: str = "unknown"
    quota_dimensions: tuple[str, ...] = ()
    quota_reset: str = "unknown"
    quota_summary: str = "No provider quota research recorded yet. Treat limits as unknown."
    quota_checked_at: str = ""
    required_config: tuple[str, ...] = ()


def provider_slug(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.casefold()).strip("-")
    return slug or "provider"


def load_catalog(path: Path = CATALOG_PATH) -> tuple[CatalogProvider, ...]:
    """Load API leads for display; malformed entries are ignored safely."""
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ()
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return ()
    result: list[CatalogProvider] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        transport = str(item.get("transport", "")).strip()
        endpoint = str(item.get("url", "")).strip()
        source_url = str(item.get("source_url", endpoint)).strip()
        name = str(item.get("name", "")).strip()
        endpoint_url = urlsplit(endpoint)
        source = urlsplit(source_url)
        if transport not in {"api", "openai-compatible", "huggingface-api", "cloudflare-workers-ai", "tokenrouter-responses"} or not name:
            continue
        if endpoint_url.scheme not in {"http", "https"} or not endpoint_url.netloc:
            continue
        if source.scheme not in {"http", "https"} or not source.netloc:
            continue
        models = item.get("models")
        if not isinstance(models, list) or not models:
            continue
        quota = item.get("quota") if isinstance(item.get("quota"), dict) else {}
        quota_status = str(quota.get("status", "unknown")).strip() or "unknown"
        quota_scope = str(quota.get("scope", "unknown")).strip() or "unknown"
        raw_dimensions = quota.get("dimensions", ())
        quota_dimensions = tuple(str(value).strip() for value in raw_dimensions if str(value).strip()) if isinstance(raw_dimensions, list) else ()
        quota_reset = str(quota.get("reset", "unknown")).strip() or "unknown"
        quota_summary = str(quota.get("summary", "No provider quota research recorded yet. Treat limits as unknown.")).strip() or "No provider quota research recorded yet. Treat limits as unknown."
        quota_checked_at = str(quota.get("checked_at", "")).strip()
        raw_required_config = item.get("required_config", ())
        required_config = tuple(str(value).strip().casefold() for value in raw_required_config if str(value).strip()) if isinstance(raw_required_config, list) else ()
        base_slug = provider_slug(name)
        for model_item in models:
            if isinstance(model_item, str):
                model = model_item.strip()
                model_name, power, quota_weight = model, "unknown", 1.0
            elif isinstance(model_item, dict):
                model = str(model_item.get("id", "")).strip()
                model_name = str(model_item.get("name", model)).strip()
                power = str(model_item.get("power", "unknown")).strip() or "unknown"
                try:
                    quota_weight = max(0.0, float(model_item.get("quota_weight", 1.0)))
                except (TypeError, ValueError):
                    quota_weight = 1.0
            else:
                continue
            if not model:
                continue
            slug = provider_slug(f"{base_slug}-{model}")
            if slug in seen:
                continue
            seen.add(slug)
            result.append(CatalogProvider(base_slug, name, slug, model_name or model, model, power, quota_weight, endpoint, source_url, transport, quota_status, quota_scope, quota_dimensions, quota_reset, quota_summary, quota_checked_at, required_config))
    return tuple(result)


def config_prefix(provider: CatalogProvider) -> str:
    return f"AIPOOL_PROVIDER_{provider.provider_slug.upper().replace('-', '_')}"


def model_config_prefix(provider: CatalogProvider) -> str:
    return f"AIPOOL_MODEL_{provider.slug.upper().replace('-', '_')}"


def provider_config_name(provider: CatalogProvider, field: str) -> str:
    """Return an allowlisted environment key for a non-secret family field."""
    normalized = str(field).strip().upper().replace("-", "_")
    if not normalized or not re.fullmatch(r"[A-Z][A-Z0-9_]*", normalized):
        raise ValueError("provider config field must be a simple identifier")
    return f"{config_prefix(provider)}_{normalized}"
