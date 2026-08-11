"""Bounded JSON HTTP gateway for local or explicitly authorized remote use."""

from __future__ import annotations

import json
import os
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit
from html import escape

from .domain import TaskEnvelope
from .benchmark import run_benchmark
from .discovered import build_discovered_adapter
from .model_discovery import classify_model, discover_models
from .provider_catalog import CatalogProvider, config_prefix, load_catalog, model_config_prefix, provider_config_name
from .queue import QueueFull, TaskQueue, record_to_dict
from .service import Coordinator
from .usage import UsageManager


MAX_BODY_BYTES = 256 * 1024
CONFIG_KEYS = frozenset({
    "AIPOOL_HF_MODEL", "AIPOOL_HF_ENDPOINT", "AIPOOL_OPENAI_ENDPOINT",
    "AIPOOL_OPENAI_MODEL", "AIPOOL_COMMAND", "AIPOOL_BROWSER_COMMAND",
    "HF_TOKEN",
    "AIPOOL_OPENAI_API_KEY", "AIPOOL_TOKEN",
})
SECRET_KEYS = frozenset({
    "HF_TOKEN", "AIPOOL_OPENAI_API_KEY", "AIPOOL_TOKEN",
})


def _provider_config_keys(provider: CatalogProvider) -> tuple[str, ...]:
    provider_prefix = config_prefix(provider)
    model_prefix = model_config_prefix(provider)
    return (
        f"{model_prefix}_ENABLED", f"{model_prefix}_MODEL",
        f"{provider_prefix}_API_KEY", f"{provider_prefix}_ENDPOINT",
        f"{provider_prefix}_REQUEST_LIMIT", f"{provider_prefix}_TOKEN_LIMIT",
        f"{provider_prefix}_USAGE_WINDOW_SECONDS",
        *(provider_config_name(provider, field) for field in provider.required_config),
    )


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
    reload_callback: Callable[[], None] | None = None,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"} and not token:
        raise ValueError("a token is required for non-loopback gateway binding")

    task_queue = queue or TaskQueue(coordinator.store, max_pending=max_pending)
    operator_config = Path(config_path or os.environ.get("AIPOOL_CONFIG_FILE", ".aipool.local")).expanduser()
    catalog = load_catalog()
    catalog_keys = frozenset(key for provider in catalog for key in _provider_config_keys(provider))
    secret_keys = SECRET_KEYS | frozenset(f"{config_prefix(provider)}_API_KEY" for provider in catalog)

    def config_snapshot() -> dict[str, object]:
        file_values: dict[str, str] = {}
        if operator_config.is_file():
            for line in operator_config.read_text().splitlines():
                key, separator, value = line.partition("=")
                if separator and key in CONFIG_KEYS | catalog_keys and value:
                    file_values[key] = value
        def value(key: str) -> str:
            return os.environ.get(key, file_values.get(key, ""))
        providers = []
        for provider in catalog:
            prefix = config_prefix(provider)
            model_prefix = model_config_prefix(provider)
            api_key_present = bool(os.environ.get(f"{prefix}_API_KEY") or file_values.get(f"{prefix}_API_KEY"))
            if not api_key_present and provider.transport == "huggingface-api":
                api_key_present = bool(os.environ.get("HF_TOKEN") or file_values.get("HF_TOKEN"))
            providers.append({
                "slug": provider.slug, "provider_slug": provider.provider_slug, "provider_name": provider.provider_name, "name": provider.name, "model": provider.model,
                "power": provider.power, "quota_weight": provider.quota_weight,
                "transport": provider.transport, "endpoint": provider.endpoint,
                "source_url": provider.source_url,
                "api_key_optional": provider.api_key_optional,
                "required_config": list(provider.required_config),
                "config_fields": [
                    {
                        "name": field,
                        "key": provider_config_name(provider, field),
                        "value": value(provider_config_name(provider, field)),
                        "present": bool(value(provider_config_name(provider, field))),
                    }
                    for field in provider.required_config
                ],
                "quota_guidance": {
                    "status": provider.quota_status,
                    "scope": provider.quota_scope,
                    "dimensions": list(provider.quota_dimensions),
                    "reset": provider.quota_reset,
                    "summary": provider.quota_summary,
                    "checked_at": provider.quota_checked_at,
                },
                "request_limit": value(f"{prefix}_REQUEST_LIMIT"),
                "token_limit": value(f"{prefix}_TOKEN_LIMIT"),
                "usage_window_seconds": value(f"{prefix}_USAGE_WINDOW_SECONDS"),
                "enabled": (
                    value(f"{model_prefix}_ENABLED").casefold() in {"1", "true", "yes", "on"}
                    if value(f"{model_prefix}_ENABLED")
                    else bool(os.environ.get(f"{prefix}_API_KEY") or file_values.get(f"{prefix}_API_KEY") or provider.api_key_optional)
                ),
                "configured_model": value(f"{model_prefix}_MODEL") or provider.model,
                "has_api_key": api_key_present,
                "adapter": (
                    "openai-compatible" if provider.transport == "openai-compatible"
                    else "cloudflare-workers-ai" if provider.transport == "cloudflare-workers-ai"
                    else "tokenrouter-responses" if provider.transport == "tokenrouter-responses"
                    else "manual"
                ),
            })
        return {
            "settings": {
                key: value(key) for key in sorted(CONFIG_KEYS - SECRET_KEYS)
            },
            "secrets": {key: bool(os.environ.get(key) or file_values.get(key)) for key in sorted(secret_keys)},
            "providers": providers,
            "discovered_models": [
                {
                    "model_key": row["model_key"], "provider_slug": row["provider_slug"], "provider_name": row["provider_name"],
                    "model_id": row["model_id"], "power": row["power"],
                    "quota_weight": row["quota_weight"],
                    "capabilities": json.loads(row["capabilities_json"]),
                    "metadata_confidence": row["metadata_confidence"],
                    "state": row["state"], "last_seen": row["last_seen"],
                    "review_note": row["review_note"], "reviewed_at": row["reviewed_at"],
                    "probe_status": row["probe_status"],
                    "probe": json.loads(row["probe_json"]), "probed_at": row["probed_at"],
                }
                for row in coordinator.store.discovered_model_rows()
            ],
            "config_path": str(operator_config),
            "restart_required": reload_callback is None,
        }

    def readiness_snapshot() -> dict[str, object]:
        """Return a redacted, no-network report for operator approval decisions."""
        now = time.time()
        configured = {str(row["slug"]): row for row in config_snapshot()["providers"]}
        effective = coordinator.health.profiles(adapter.profile for adapter in coordinator.registry.all())
        by_id = {profile.id: profile for profile in effective}
        rows: list[dict[str, object]] = []
        for provider in catalog:
            card = configured[provider.slug]
            key_present = bool(card["has_api_key"])
            enabled = bool(card["enabled"])
            profile = by_id.get(f"catalog:{provider.slug}")
            request_limit = int(str(card["request_limit"] or 0))
            token_limit = int(str(card["token_limit"] or 0))
            window_seconds = float(str(card["usage_window_seconds"] or 60))
            if profile is not None:
                window_start, window_end = UsageManager.window(profile, now)
                requests, tokens = coordinator.store.usage(profile.quota_group, window_start)
                state = profile.state.value
                next_probe_at = coordinator.store.health(profile.id)
                next_probe = float(next_probe_at["next_probe_at"]) if next_probe_at else 0.0
            else:
                window_end = (now // window_seconds + 1) * window_seconds
                requests, tokens, state, next_probe = 0, 0, "not_loaded", 0.0
            reasons = []
            if not key_present:
                reasons.append("missing_api_key")
            missing_config = [field["name"] for field in card["config_fields"] if not field["present"]]
            if missing_config:
                reasons.append("missing_required_config")
            if not enabled:
                reasons.append("disabled")
            if profile is None and key_present and enabled:
                reasons.append("not_loaded")
            if state in {"rate_limited", "auth_required", "broken", "degraded"}:
                reasons.append(state)
            benchmark = coordinator.store.latest_benchmark(f"catalog:{provider.slug}")
            rows.append({
                "slug": provider.slug,
                "provider_slug": provider.provider_slug,
                "name": provider.name,
                "power": provider.power,
                "transport": provider.transport,
                "quota_weight": provider.quota_weight,
                "key_present": key_present,
                "enabled": enabled,
                "loaded": profile is not None,
                "state": state,
                "request_limit": request_limit,
                "requests_used": requests,
                "token_limit": token_limit,
                "tokens_used": tokens,
                "window_ends_at": window_end,
                "next_probe_at": next_probe,
                "smoke_test_requires_approval": True,
                "blocked_reasons": reasons,
                "last_benchmark": benchmark,
            })
        counts = {state: sum(1 for row in rows if row["state"] == state) for state in sorted({str(row["state"]) for row in rows})}
        return {"generated_at": now, "providers": rows, "summary": {"total": len(rows), "states": counts}}

    def smoke_batch_plan(max_models: int = 12, cases: int = 3) -> dict[str, object]:
        """Choose a bounded, reviewable smoke batch without contacting providers."""
        if not 1 <= max_models <= 32:
            raise ValueError("max_models must be between 1 and 32")
        if not 1 <= cases <= 8:
            raise ValueError("cases must be between 1 and 8")
        rank = {"light": 0, "medium": 1, "strong": 2, "very-strong": 3}
        snapshot = readiness_snapshot()
        families: dict[str, list[dict[str, object]]] = {}
        for row in snapshot["providers"]:
            families.setdefault(str(row["provider_slug"]), []).append(row)
        candidates: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        for family, items in families.items():
            eligible = [
                item for item in items
                if item["loaded"] and item["enabled"] and item["state"] in {"healthy", "quarantined"}
            ]
            if not eligible:
                skipped.append({"provider_slug": family, "reason": "no_loaded_enabled_candidate"})
                continue
            selected = max(eligible, key=lambda item: (rank.get(str(item["power"]), -1), -float(item["quota_weight"])))
            candidates.append(selected)
        candidates.sort(key=lambda item: (-rank.get(str(item["power"]), -1), str(item["provider_slug"]), str(item["slug"])))
        selected = candidates[:max_models]
        for item in candidates[max_models:]:
            skipped.append({"provider_slug": item["provider_slug"], "slug": item["slug"], "reason": "batch_cap"})
        models = []
        for item in selected:
            request_limit = int(item["request_limit"])
            requests_used = int(item["requests_used"])
            models.append({
                "slug": item["slug"], "provider_slug": item["provider_slug"],
                "name": item["name"], "power": item["power"], "state": item["state"],
                "quota_weight": item["quota_weight"], "cases": cases,
                "expected_requests": cases, "request_limit": request_limit,
                "requests_used": requests_used,
                "request_headroom": max(0, request_limit - requests_used) if request_limit else None,
                "token_limit": int(item["token_limit"]), "tokens_used": int(item["tokens_used"]),
                "window_ends_at": item["window_ends_at"],
            })
        return {
            "network_calls_made": False, "approval_required": True,
            "cases": cases, "max_models": max_models,
            "selected_models": models, "skipped": skipped,
            "total_models": len(models), "expected_calls": len(models) * cases,
            "token_cost": "unknown until provider responses; local token caps are reported per model",
        }

    def save_config(updates: dict[str, str]) -> None:
        operator_config.parent.mkdir(parents=True, exist_ok=True)
        existing = operator_config.read_text() if operator_config.exists() else ""
        lines = existing.splitlines()
        existing_keys = {
            key for line in lines
            for key, separator, _ in [line.partition("=")]
            if separator
        }
        # A new family key is useful only if its model cards are enabled by
        # default. An explicit toggle from the panel still wins.
        for provider in catalog:
            family_key = f"{config_prefix(provider)}_API_KEY"
            model_key = f"{model_config_prefix(provider)}_ENABLED"
            if family_key in updates and model_key not in updates and model_key not in existing_keys:
                updates[model_key] = "1"
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

        def _redirect(self, location: str) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

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
                    {
                        "id": profile.id, "name": profile.name,
                        "transport": profile.transport,
                        "state": profile.state.value,
                        "capabilities": dict(profile.capabilities),
                    }
                    for profile in profiles
                ],
                "stats": coordinator.store.stats(),
            }

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            if self.path == "/":
                self._redirect("/admin")
                return
            if self.path == "/status":
                self._send(200, self._operational_status())
                return
            if self.path == "/admin/config":
                self._send(200, config_snapshot())
                return
            if self.path == "/admin/readiness":
                self._send(200, readiness_snapshot())
                return
            parsed_path = urlsplit(self.path)
            if parsed_path.path == "/admin/provider/smoke-batch-plan":
                try:
                    query = parse_qs(parsed_path.query)
                    max_models = int(query.get("max_models", ["12"])[0])
                    cases = int(query.get("cases", ["3"])[0])
                    self._send(200, smoke_batch_plan(max_models=max_models, cases=cases))
                except (TypeError, ValueError) as exc:
                    self._send(400, {"error": str(exc)[:300]})
                return
            if parsed_path.path == "/admin/discover-models":
                slug = parse_qs(parsed_path.query).get("slug", [""])[0]
                provider = next((item for item in catalog if item.slug == slug), None)
                if provider is None:
                    self._send(404, {"success": False, "error": "unknown_model"})
                    return
                config_prefix_value = config_prefix(provider)
                api_key = os.environ.get(f"{config_prefix_value}_API_KEY", "")
                if not api_key and provider.transport == "huggingface-api":
                    api_key = os.environ.get("HF_TOKEN", "")
                endpoint = os.environ.get(f"{config_prefix_value}_ENDPOINT") or provider.endpoint
                result = discover_models(endpoint, api_key, api_key_optional=provider.api_key_optional)
                models = [classify_model(model) for model in result.models]
                if result.success:
                    coordinator.store.save_discovered_models(provider, models, now=time.time())
                self._send(200, {"success": result.success, "models": models, "endpoint": result.endpoint, "error": result.error, "persisted": len(models) if result.success else 0})
                return
            if self.path == "/admin":
                self._send_html(200, """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>aipool / provider console</title><style>
:root{color-scheme:dark;--bg:#101414;--panel:#182020;--panel2:#202b2a;--ink:#e9f0e9;--muted:#9eafaa;--line:#33423f;--accent:#c5f36b;--warn:#ffcf70;--bad:#ff8f86}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% -10%,#2d463a 0,#101414 42%);color:var(--ink);font:16px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}main{max-width:1120px;margin:0 auto;padding:52px 24px 80px}header{display:flex;justify-content:space-between;gap:24px;align-items:end;border-bottom:1px solid var(--line);padding-bottom:28px;margin-bottom:30px}h1{font:800 clamp(2rem,5vw,4.5rem)/.95 Georgia,serif;letter-spacing:-.06em;margin:0;max-width:650px}h1 span{color:var(--accent)}h2{font-size:1rem;letter-spacing:.08em;text-transform:uppercase;color:var(--accent);margin:34px 0 14px}.lede{color:var(--muted);max-width:720px}.signal{color:var(--warn);font-size:.8rem;text-align:right}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:14px}.card{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 12px 32px #0003}.card header{border:0;padding:0;margin:0 0 16px;align-items:start}.card h3{margin:0;font-size:1rem}.tag{display:inline-block;border:1px solid #536558;border-radius:99px;color:var(--accent);font-size:.7rem;padding:2px 8px;margin-top:5px}.meta{color:var(--muted);font-size:.75rem;margin:10px 0 16px}.meta a{color:var(--accent)}label{display:block;color:var(--muted);font-size:.75rem;margin:12px 0 5px}input{width:100%;background:#0d1212;border:1px solid var(--line);border-radius:7px;color:var(--ink);padding:10px;font:inherit;font-size:.85rem}input:focus,button:focus{outline:2px solid var(--accent);outline-offset:2px}.toggle{display:flex;gap:9px;align-items:center;color:var(--ink)}.toggle input{width:auto;accent-color:var(--accent)}button{border:0;border-radius:8px;background:var(--accent);color:#111a13;padding:12px 18px;font:800 .85rem ui-monospace;cursor:pointer}.actions{display:flex;align-items:center;gap:16px;margin-top:24px}.status{color:var(--muted);font-size:.8rem}.advanced{background:#0d1212;border:1px solid var(--line);padding:18px;border-radius:12px}.advanced .grid{grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}small{color:var(--muted)}.family-card{grid-column:1/-1}.family-card.configured{border-color:#607f4b}.family-card .family-header{border:0;padding:0;margin:0 0 10px;align-items:start}.family-card .family-header h3{font-size:1.1rem}.family-card .family-actions{display:flex;align-items:center;gap:12px;margin-top:14px}.model-details{margin-top:16px}.model-details[hidden]{display:none}.model-details .grid{grid-template-columns:repeat(auto-fit,minmax(270px,1fr))}.setup-needed{color:var(--warn);font-size:.72rem;border:1px solid #806b3d;border-radius:99px;padding:3px 8px}.provider-issue{color:var(--bad);border:1px solid #824b47;border-radius:9px;padding:10px;font-size:.75rem}.provider-issue strong{color:var(--warn)}.family-key{max-width:600px}@media(max-width:560px){main{padding:28px 14px}header{display:block}.signal{text-align:left;margin-top:16px}}
.key-status{display:inline-flex;align-items:center;gap:5px;border-radius:99px;font-size:.68rem;padding:3px 8px;margin-top:8px}.key-status::before{content:'●';font-size:.6rem}.key-set{color:var(--accent);border:1px solid #607f4b}.key-unset{color:var(--muted);border:1px solid var(--line)}.savebar{position:fixed;left:50%;bottom:20px;transform:translate(-50%,140%);transition:transform .2s ease;z-index:5;width:min(720px,calc(100% - 28px));display:flex;justify-content:space-between;align-items:center;gap:16px;background:#182020f5;border:1px solid #607f4b;border-radius:12px;padding:12px 14px;backdrop-filter:blur(12px);box-shadow:0 12px 32px #0006}.savebar.is-dirty{transform:translate(-50%,0)}.savebar strong{font-size:.8rem}.savebar span{color:var(--muted);font-size:.72rem}@media(max-width:560px){.savebar{bottom:8px}.savebar span{display:block;font-size:.65rem}}</style></head><body><main><header><div><h1>provider<br><span>console</span></h1><p class="lede">Configure free-tier compute by model, not by brand. Every card remains quarantined until its smoke test proves capability and quota economics.</p></div><div class="signal">LOCAL OPERATOR PANEL<br>SECRETS NEVER ECHOED</div></header><form id="f"><div class="savebar"><div><strong>Hey! Looks like you made some changes. Wanna save 'em?</strong><br><span>Changes apply immediately.</span></div><button type="submit">Save configuration</button></div><section><h2>Readiness — no provider calls</h2><div id="readiness" class="advanced"><p class="status">Loading readiness…</p></div></section><section><h2>Model pool</h2><div id="providers" class="grid"><p class="status">Loading catalog…</p></div></section><section><h2>Advanced bridges</h2><div class="advanced"><div class="grid"><div><label for="hfmodel">Legacy HF model</label><input id="hfmodel" name="AIPOOL_HF_MODEL" placeholder="Use a model card above"></div><div><label for="hftoken">HF token</label><input id="hftoken" name="HF_TOKEN" type="password" autocomplete="new-password" placeholder="Leave blank to keep current"></div><div><label for="endpoint">Custom OpenAI-compatible endpoint</label><input id="endpoint" name="AIPOOL_OPENAI_ENDPOINT"></div><div><label for="openmodel">Custom model</label><input id="openmodel" name="AIPOOL_OPENAI_MODEL"></div></div></div></section><div class="actions"><button type="submit">Save configuration</button><span id="o" class="status" role="status"></span></div></form></main><script>
const form=document.querySelector('#f'),cards=document.querySelector('#providers'),out=document.querySelector('#o'),savebar=document.querySelector('.savebar');
const readiness=document.querySelector('#readiness');
const key=(slug,suffix)=>'AIPOOL_MODEL_'+slug.toUpperCase().replaceAll('-','_')+'_'+suffix;
const providerKey=(slug,suffix)=>'AIPOOL_PROVIDER_'+slug.toUpperCase().replaceAll('-','_')+'_'+suffix;
function quotaGuidance(p){let q=p.quota_guidance||{};return `<div class="quota-guidance"><strong>Quota guidance</strong><br>${esc(q.summary||'Unknown — research this provider before setting caps.')}<br><small>Scope: ${esc(q.scope||'unknown')} · dimensions: ${esc((q.dimensions||[]).join(', ')||'unknown')} · reset: ${esc(q.reset||'unknown')}${q.checked_at?' · checked '+esc(q.checked_at):''}</small></div>`}
function issueGuidance(items){let issues=items.flatMap(p=>(p.blocked_reasons||[]).map(reason=>({p,reason})));for(let p of items){let b=p.last_benchmark;if(b&&b.valid<b.attempts)issues.push({p,reason:'benchmark_failed',benchmark:b})}if(!issues.length)return '';let unique=[...new Map(issues.map(x=>[x.p.slug+'|'+x.reason,x])).values()];let text=unique.map(({p,reason,benchmark})=>{let advice=reason==='auth_required'?'Next step: verify the API key, endpoint, and model access. Recommendation: keep this model paused until authentication succeeds.':reason==='rate_limited'?'Next step: wait until the provider reset time. Recommendation: keep it on hold and route elsewhere.':reason==='broken'?'Next step: inspect the recorded error and provider status. Recommendation: disable it until fixed.':reason==='degraded'?'Next step: review recent smoke results and latency. Recommendation: use only for low-risk/simple work for now.':reason==='benchmark_failed'?`Smoke result: ${benchmark.valid}/${benchmark.attempts} valid. Next step: inspect the model details and repeat only after reviewing the failure. Recommendation: do not route complex work to it yet.`:reason==='missing_required_config'?'Next step: save the required provider field shown on this card. Recommendation: keep the provider unloaded until its account metadata is complete.':reason==='not_loaded'&&p.has_api_key?'Next step: this key is saved, but the provider adapter, account metadata, endpoint, or model ID is not ready. Recommendation: keep it out of routing until the integration is completed and verified.':reason==='not_loaded'?'Next step: save a key and enable the model, then reload readiness. Recommendation: it cannot receive work yet.':reason==='missing_api_key'?'Next step: add the family key. Recommendation: it cannot receive work yet.':reason==='disabled'?'Next step: enable the model only after reviewing its quota and capability. Recommendation: leave disabled until then.':'Next step: review this status before routing.';return `<div class="provider-issue"><strong>${esc(p.name)} · ${esc(reason)}</strong><br>${advice}</div>`}).join('');return `<div class="provider-issues"><strong>Action needed</strong>${text}</div>`}
function card(p){let limits=p.showLimits?`<div class="quota-box"><p class="meta">Local safety caps — not the provider’s advertised quota</p><label>Requests per window<input data-key="${providerKey(p.provider_slug,'REQUEST_LIMIT')}" value="${esc(p.request_limit)}" inputmode="numeric" placeholder="0 = unknown"></label><label>Tokens per window<input data-key="${providerKey(p.provider_slug,'TOKEN_LIMIT')}" value="${esc(p.token_limit)}" inputmode="numeric" placeholder="0 = unknown"></label><label>Window seconds<input data-key="${providerKey(p.provider_slug,'USAGE_WINDOW_SECONDS')}" value="${esc(p.usage_window_seconds)}" inputmode="decimal"></label></div>`:'';return `<article class="card"><header><div><h3>${esc(p.name)}</h3><span class="tag">${esc(p.power)} · quota ×${p.quota_weight}</span><br><span class="status">${p.enabled?'enabled':'disabled'} · ${esc(p.state)}</span></div><label class="toggle"><input type="checkbox" data-provider="${esc(p.provider_slug)}" data-key="${key(p.slug,'ENABLED')}" ${p.enabled?'checked':''}> enable</label></header><p class="meta"><a href="${esc(p.source_url)}" target="_blank" rel="noreferrer">source</a> · ${esc(p.transport)} · default model: ${esc(p.model)}</p>${limits}<label>Model ID<input data-key="${key(p.slug,'MODEL')}" value="${esc(p.configured_model)}"></label><button type="button" onclick="refreshModels('${p.slug}')">Refresh live model list</button> <button type="button" onclick="smokeProvider('${p.slug}')">Run bounded smoke test</button><span class="status" id="live-${p.slug}"></span><span class="status" id="smoke-${p.slug}"></span></article>`}
function toggleFamily(button,id,count){let panel=document.getElementById(id),closed=panel.hasAttribute('hidden');if(closed)panel.removeAttribute('hidden');else panel.setAttribute('hidden','');button.setAttribute('aria-expanded',String(closed));button.textContent=(closed?'Hide':'View')+' model details ('+count+')'}
function configFields(p){return (p.config_fields||[]).map(f=>{let label=f.name==='account_id'?'Cloudflare account ID':f.name.replaceAll('_',' ');let help=f.name==='account_id'?'Find this in the Cloudflare dashboard URL or account overview. It is not a secret.':'';return `<label class="family-key">${esc(label)} ${f.present?'(saved; leave blank to preserve)':''}<input data-provider="${esc(p.provider_slug)}" data-key="${esc(f.key)}" value="" placeholder="${esc(help||'required provider setting')}" autocomplete="off">${help?`<small>${esc(help)}</small>`:''}</label>`}).join('')}
function familyCard(group){let first=group.items[0],family=first.provider_slug,hasKey=group.items.some(p=>p.has_api_key),optional=group.items.some(p=>p.api_key_optional),enabled=group.items.filter(p=>p.enabled).length,detailsId='family-details-'+family;return `<article class="card family-card ${hasKey||optional?'configured':''}"><header class="family-header"><div><h3>${esc(group.name)}</h3><span class="tag">${group.items.length} models · ${enabled} enabled</span><br>${hasKey?'<span class="key-status key-set">API key saved</span>':optional?'<span class="key-status key-set">Anonymous free access</span>':'<span class="setup-needed">Setup needed</span>'}</div></header><p class="meta">${hasKey?'Provider configured; model details are collapsed.':optional?'This provider documents anonymous access for selected free models; an API key is optional.':'Add one API key to configure this provider family.'}<br>${esc(first.transport)} · <a href="${esc(first.source_url)}" target="_blank" rel="noreferrer">official source</a></p>${issueGuidance(group.items)}${quotaGuidance(first)}<label class="family-key">API key ${hasKey?'(saved; leave blank to preserve)':optional?'(optional; leave blank for anonymous access)':''}<input type="password" data-provider="${esc(family)}" autocomplete="new-password" data-key="${providerKey(family,'API_KEY')}" placeholder="${optional?'optional key for ':'one key for '}${esc(group.name)}"></label>${configFields(first)}<div class="family-actions"><button type="button" aria-expanded="false" onclick="toggleFamily(this,'${detailsId}',${group.items.length})">View model details (${group.items.length})</button></div><div id="${detailsId}" class="model-details" hidden><div class="grid">${group.items.map(card).join('')}</div></div></article>`}

function esc(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function loadReadiness(){let r=await fetch('/admin/readiness');let d=await r.json();let states=Object.entries(d.summary.states).map(([state,count])=>count+' '+state).join(' · ');readiness.innerHTML='<p class="meta">'+d.summary.total+' catalog models · '+states+'</p><p class="status">No provider calls were made. Review key, quota, and health state before running a smoke test.</p>'}
async function refreshModels(slug){let node=document.querySelector('#live-'+slug);node.textContent=' checking…';let r=await fetch('/admin/discover-models?slug='+encodeURIComponent(slug));let d=await r.json();node.textContent=d.success?' live '+d.models.length+' models: '+d.models.slice(0,5).map(m=>m.id+' ['+m.power+']').join(', '):( ' '+(d.error||'unavailable'));}
async function smokeProvider(slug){let node=document.querySelector('#smoke-'+slug);node.textContent=' testing…';let r=await fetch('/admin/provider/smoke-test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slug,operator_approved:true})});let d=await r.json();node.textContent=r.ok?' smoke '+d.valid+'/'+d.attempts+' valid · '+d.state:' '+(d.error||'smoke test failed');}
async function reviewModel(encoded,decision){let key=decodeURIComponent(encoded);let input=Array.from(document.querySelectorAll('[data-review-model]')).find(el=>el.dataset.reviewModel===key);let note=input?.value.trim()||'';if(!note){alert('Add a short review note before deciding.');return}let r=await fetch('/admin/discovered-model/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model_key:key,decision,note})});let d=await r.json();if(!r.ok){alert(d.error||'Review failed');return}await load()}
async function smokeTestModel(encoded){let key=decodeURIComponent(encoded);let r=await fetch('/admin/discovered-model/smoke-test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model_key:key})});let d=await r.json();if(!r.ok){alert(d.error||'Smoke test failed');return}await load()}
async function activationChange(encoded,path,attribute){let key=decodeURIComponent(encoded);let input=Array.from(document.querySelectorAll('['+attribute+']')).find(el=>el.getAttribute(attribute)===key);let note=input?.value.trim()||'';if(!note){alert('Add a short decision note first.');return}let r=await fetch('/admin/discovered-model/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model_key:key,note})});let d=await r.json();if(!r.ok){alert(d.error||'Activation change failed');return}await load()}
async function activateModel(encoded){return activationChange(encoded,'activate','data-activation-model')}
async function deactivateModel(encoded){return activationChange(encoded,'deactivate','data-deactivation-model')}
async function load(){let r=await fetch('/admin/config');let c=await r.json();let health=(await (await fetch('/admin/readiness')).json()).providers;let bySlug=Object.fromEntries(health.map(p=>[p.slug,p]));c.providers.forEach(p=>Object.assign(p,bySlug[p.slug]||{}));document.querySelector('#hfmodel').value=c.settings.AIPOOL_HF_MODEL||'';document.querySelector('#endpoint').value=c.settings.AIPOOL_OPENAI_ENDPOINT||'';document.querySelector('#openmodel').value=c.settings.AIPOOL_OPENAI_MODEL||'';let groups={};for(let p of c.providers)(groups[p.provider_slug]??={name:p.provider_name,items:[]}).items.push(p);for(let g of Object.values(groups))g.items.forEach((p,i)=>p.showLimits=i===0);let catalog=Object.values(groups).sort((a,b)=>Number(a.items[0].has_api_key)-Number(b.items[0].has_api_key)).map(familyCard).join('');let findings=c.discovered_models?.length?`<section><h3>Live findings — human review required</h3><div class="grid">${c.discovered_models.slice(0,96).map(m=>{let actions=m.state==='quarantined'?`<label>Review note<input data-review-model="${esc(m.model_key)}" placeholder="identity, capability, quota evidence"></label><button type="button" onclick="reviewModel('${encodeURIComponent(m.model_key)}','approve')">Approve for bounded smoke test</button> <button type="button" onclick="reviewModel('${encodeURIComponent(m.model_key)}','reject')">Reject</button>`:m.state==='approved'?`<button type="button" onclick="smokeTestModel('${encodeURIComponent(m.model_key)}')">Run bounded smoke test</button>`:m.state==='smoke_tested'?`<label>Activation note<input data-activation-model="${esc(m.model_key)}" placeholder="why this should route"></label><button type="button" onclick="activateModel('${encodeURIComponent(m.model_key)}')">Activate routing</button>`:m.state==='active'?`<label>Rollback note<input data-deactivation-model="${esc(m.model_key)}" placeholder="why to pause it"></label><button type="button" onclick="deactivateModel('${encodeURIComponent(m.model_key)}')">Disable routing</button>`:`<span class="status">${esc(m.state)} · ${esc(m.probe_status)} — activation still requires explicit approval</span>`;return `<article class="card"><h3>${esc(m.model_id)}</h3><span class="tag">${esc(m.power)} · quota ×${m.quota_weight} · ${esc(m.metadata_confidence)} confidence</span><p class="meta">${esc(m.provider_name)} · ${esc(m.state)} · capabilities: ${esc(m.capabilities.join(', '))}<br>heuristics never grant complex routing</p>${actions}</article>`}).join('')}</div></section>`:'';cards.innerHTML=(catalog||'<p class="status">No API models in the catalog.</p>')+findings}
form.addEventListener('input',e=>{savebar.classList.add('is-dirty');let el=e.target;if(el.dataset.provider&&el.value){for(let box of form.querySelectorAll('input[type="checkbox"][data-provider="'+el.dataset.provider+'"]'))box.checked=true}});form.onsubmit=async e=>{e.preventDefault();let payload={};for(let el of form.querySelectorAll('[data-key],input[name]')){let k=el.dataset.key||el.name;if(el.type==='password'&&!el.value)continue;payload[k]=el.type==='checkbox'?(el.checked?'1':'0'):el.value}let r=await fetch('/admin/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});let data=await r.json();if(data.updated)savebar.classList.remove('is-dirty');out.textContent=data.updated?'Saved and applied immediately.':(data.error||'Save failed')};loadReadiness();load();
</script></body></html>""")
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
                        if str(key) in CONFIG_KEYS | catalog_keys and isinstance(value, str) and value
                    }
                    save_config(updates)
                    reloaded = False
                    if reload_callback is not None:
                        reload_callback()
                        reloaded = True
                except (ValueError, TypeError, KeyError, json.JSONDecodeError, OSError) as exc:
                    self._send(400, {"updated": False, "error": str(exc)[:300]})
                    return
                self._send(200, {"updated": True, "saved_keys": sorted(updates), "config_path": str(operator_config), "restart_required": not reloaded, "reloaded": reloaded})
                return
            if path == "/admin/discovered-model/review":
                try:
                    payload = self._read_json()
                    if not isinstance(payload, dict):
                        raise ValueError("review must be an object")
                    row = coordinator.store.review_discovered_model(
                        str(payload.get("model_key", "")),
                        str(payload.get("decision", "")),
                        str(payload.get("note", "")),
                        now=time.time(),
                    )
                except LookupError as exc:
                    self._send(404, {"error": str(exc)})
                    return
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    self._send(400, {"error": str(exc)[:300]})
                    return
                self._send(200, {
                    "model_key": row["model_key"], "model_id": row["model_id"],
                    "state": row["state"], "review_note": row["review_note"],
                    "reviewed_at": row["reviewed_at"],
                })
                return
            if path in {"/admin/discovered-model/activate", "/admin/discovered-model/deactivate"}:
                try:
                    payload = self._read_json()
                    if not isinstance(payload, dict):
                        raise ValueError("activation must be an object")
                    model_key = str(payload.get("model_key", ""))
                    note = str(payload.get("note", ""))
                    if path.endswith("/activate"):
                        row = coordinator.store.activate_discovered_model(model_key, note, now=time.time())
                    else:
                        row = coordinator.store.deactivate_discovered_model(model_key, note, now=time.time())
                    reloaded = False
                    if reload_callback is not None:
                        reload_callback()
                        reloaded = True
                except LookupError as exc:
                    self._send(404, {"error": str(exc)})
                    return
                except (ValueError, TypeError, json.JSONDecodeError, OSError) as exc:
                    self._send(400, {"error": str(exc)[:300]})
                    return
                self._send(200, {
                    "model_key": row["model_key"], "model_id": row["model_id"],
                    "state": row["state"], "reloaded": reloaded,
                    "restart_required": not reloaded,
                })
                return
            if path == "/admin/discovered-model/smoke-test":
                try:
                    payload = self._read_json()
                    if not isinstance(payload, dict):
                        raise ValueError("smoke test must be an object")
                    model_key = str(payload.get("model_key", "")).strip()
                    row = next((item for item in coordinator.store.discovered_model_rows() if item["model_key"] == model_key), None)
                    if row is None:
                        raise LookupError("unknown_discovered_model")
                    if row["state"] != "approved":
                        raise ValueError("discovered model must be approved before smoke testing")
                    provider = next((item for item in catalog if item.provider_slug == row["provider_slug"]), None)
                    if provider is None:
                        raise ValueError("provider family is not in the current catalog")
                    provider_prefix = config_prefix(provider)
                    api_key_env = f"{provider_prefix}_API_KEY"
                    if not os.environ.get(api_key_env):
                        api_key = next((line.partition("=")[2] for line in operator_config.read_text().splitlines()
                                        if line.partition("=")[0] == api_key_env), "") if operator_config.is_file() else ""
                        if api_key:
                            os.environ[api_key_env] = api_key
                    if not os.environ.get(api_key_env) and row["transport"] == "huggingface-api":
                        api_key_env = "HF_TOKEN"
                    def limit(name: str, default: str) -> str:
                        if os.environ.get(name):
                            return os.environ[name]
                        if operator_config.is_file():
                            for line in operator_config.read_text().splitlines():
                                key, separator, value = line.partition("=")
                                if separator and key == name:
                                    return value
                        return default
                    adapter = build_discovered_adapter(
                        row, api_key_env=api_key_env,
                        request_limit=max(0, int(limit(f"{provider_prefix}_REQUEST_LIMIT", "0"))),
                        token_limit=max(0, int(limit(f"{provider_prefix}_TOKEN_LIMIT", "0"))),
                        usage_window_seconds=max(0.001, float(limit(f"{provider_prefix}_USAGE_WINDOW_SECONDS", "60"))),
                    )
                    result = run_benchmark(adapter)
                    coordinator.store.record_benchmark(result)
                    if result.stopped_error is not None:
                        coordinator.health.failure(adapter.profile, result.stopped_error, "discovered smoke test stopped", retry_after_seconds=result.retry_after_seconds)
                    row = coordinator.store.record_discovered_probe(model_key, result, now=time.time())
                except LookupError as exc:
                    self._send(404, {"error": str(exc)})
                    return
                except (ValueError, TypeError, json.JSONDecodeError, OSError) as exc:
                    self._send(400, {"error": str(exc)[:300]})
                    return
                self._send(200, {
                    "model_key": row["model_key"], "model_id": row["model_id"],
                    "provider_id": adapter.profile.id, "state": row["state"],
                    "probe_status": row["probe_status"], "probe": json.loads(row["probe_json"]),
                })
                return
            if path == "/admin/provider/smoke-test":
                try:
                    payload = self._read_json()
                    if not isinstance(payload, dict):
                        raise ValueError("provider smoke test must be an object")
                    if payload.get("operator_approved") is not True:
                        raise ValueError("explicit operator approval is required before a provider smoke test")
                    slug = str(payload.get("slug", "")).strip()
                    provider = next((item for item in catalog if item.slug == slug), None)
                    if provider is None:
                        raise LookupError("unknown_catalog_model")
                    provider_id = f"catalog:{provider.slug}"
                    adapter = next((item for item in coordinator.registry.all() if item.profile.id == provider_id), None)
                    if adapter is None:
                        raise RuntimeError("provider_not_loaded; save its API key and enable the model first")
                    result = coordinator.benchmark_provider(provider_id)
                    state = next(
                        (profile.state.value for profile in coordinator.health.profiles((adapter.profile,))),
                        adapter.profile.state.value,
                    )
                except LookupError as exc:
                    self._send(404, {"error": str(exc)})
                    return
                except RuntimeError as exc:
                    self._send(409, {"error": str(exc)})
                    return
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    self._send(400, {"error": str(exc)[:300]})
                    return
                self._send(200, {
                    "provider_id": result.provider_id, "attempts": result.attempts,
                    "valid": result.valid, "scores": result.scores,
                    "stopped_error": result.stopped_error,
                    "retry_after_seconds": result.retry_after_seconds, "state": state,
                })
                return
            if path == "/admin/provider/smoke-batch":
                try:
                    payload = self._read_json()
                    if not isinstance(payload, dict) or payload.get("operator_approved") is not True:
                        raise ValueError("explicit operator approval is required before a provider smoke batch")
                    slugs = payload.get("slugs")
                    if not isinstance(slugs, list) or not slugs or len(slugs) > 12 or any(not isinstance(slug, str) or not slug.strip() for slug in slugs):
                        raise ValueError("smoke batch must contain 1 to 12 model slugs")
                    if len(set(slugs)) != len(slugs):
                        raise ValueError("smoke batch slugs must be unique")
                    readiness = {str(row["slug"]): row for row in readiness_snapshot()["providers"]}
                    providers = []
                    for slug in slugs:
                        row = readiness.get(slug)
                        if row is None:
                            raise LookupError(f"unknown_catalog_model: {slug}")
                        if not row["loaded"] or not row["enabled"] or row["state"] not in {"healthy", "quarantined"}:
                            raise RuntimeError(f"provider_not_ready: {slug} ({row['state']})")
                        if row["request_limit"] and row["requests_used"] + 3 > row["request_limit"]:
                            raise RuntimeError(f"request_quota_headroom_insufficient: {slug}")
                        providers.append(f"catalog:{slug}")
                    results = coordinator.benchmark_providers(tuple(providers))
                except LookupError as exc:
                    self._send(404, {"error": str(exc)})
                    return
                except RuntimeError as exc:
                    self._send(409, {"error": str(exc)})
                    return
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    self._send(400, {"error": str(exc)[:300]})
                    return
                self._send(200, {
                    "operator_approved": True, "sequential": True,
                    "results": [
                        {"provider_id": result.provider_id, "attempts": result.attempts,
                         "valid": result.valid, "scores": result.scores,
                         "stopped_error": result.stopped_error,
                         "retry_after_seconds": result.retry_after_seconds}
                        for result in results.values()
                    ],
                })
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
