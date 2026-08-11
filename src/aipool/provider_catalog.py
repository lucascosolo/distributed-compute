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
        if transport not in {"api", "openai-compatible", "huggingface-api"} or not name:
            continue
        if endpoint_url.scheme not in {"http", "https"} or not endpoint_url.netloc:
            continue
        if source.scheme not in {"http", "https"} or not source.netloc:
            continue
        models = item.get("models")
        if not isinstance(models, list) or not models:
            continue
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
            result.append(CatalogProvider(base_slug, name, slug, model_name or model, model, power, quota_weight, endpoint, source_url, transport))
    return tuple(result)


def config_prefix(provider: CatalogProvider) -> str:
    return f"AIPOOL_PROVIDER_{provider.provider_slug.upper().replace('-', '_')}"


def model_config_prefix(provider: CatalogProvider) -> str:
    return f"AIPOOL_MODEL_{provider.slug.upper().replace('-', '_')}"
