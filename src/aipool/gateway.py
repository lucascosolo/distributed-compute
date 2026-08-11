"""Bounded JSON HTTP gateway for local or explicitly authorized remote use."""

from __future__ import annotations

import json
import os
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from html import escape

from .domain import TaskEnvelope
from .model_discovery import classify_model, discover_models
from .provider_catalog import CatalogProvider, config_prefix, load_catalog, model_config_prefix
from .queue import QueueFull, TaskQueue, record_to_dict
from .service import Coordinator


MAX_BODY_BYTES = 256 * 1024
CONFIG_KEYS = frozenset({
    "AIPOOL_HF_MODEL", "AIPOOL_HF_ENDPOINT", "AIPOOL_OPENAI_ENDPOINT",
    "AIPOOL_OPENAI_MODEL", "AIPOOL_COMMAND", "AIPOOL_BROWSER_COMMAND",
    "AIPOOL_DISCORD_APPLICATION_ID", "AIPOOL_DISCORD_GUILD_ID",
    "AIPOOL_DISCORD_CHANNEL_ID", "AIPOOL_DISCORD_MESSAGE_PREFIX",
    "AIPOOL_DISCORD_BOT_TOKEN", "HF_TOKEN",
    "AIPOOL_OPENAI_API_KEY", "AIPOOL_TOKEN",
})
SECRET_KEYS = frozenset({
    "HF_TOKEN", "AIPOOL_OPENAI_API_KEY", "AIPOOL_TOKEN", "AIPOOL_DISCORD_BOT_TOKEN",
})


def _provider_config_keys(provider: CatalogProvider) -> tuple[str, ...]:
    provider_prefix = config_prefix(provider)
    model_prefix = model_config_prefix(provider)
    return (f"{model_prefix}_ENABLED", f"{model_prefix}_MODEL", f"{provider_prefix}_API_KEY", f"{provider_prefix}_ENDPOINT")


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
            providers.append({
                "slug": provider.slug, "provider_slug": provider.provider_slug, "provider_name": provider.provider_name, "name": provider.name, "model": provider.model,
                "power": provider.power, "quota_weight": provider.quota_weight,
                "transport": provider.transport, "endpoint": provider.endpoint,
                "source_url": provider.source_url,
                "enabled": value(f"{model_prefix}_ENABLED").casefold() in {"1", "true", "yes", "on"},
                "configured_model": value(f"{model_prefix}_MODEL") or provider.model,
                "has_api_key": bool(os.environ.get(f"{prefix}_API_KEY") or file_values.get(f"{prefix}_API_KEY")),
                "adapter": "openai-compatible" if provider.transport == "openai-compatible" else "manual",
            })
        return {
            "settings": {
                key: value(key) for key in sorted(CONFIG_KEYS - SECRET_KEYS)
            },
            "secrets": {key: bool(os.environ.get(key) or file_values.get(key)) for key in sorted(secret_keys)},
            "providers": providers,
            "discovered_models": [
                {
                    "provider_slug": row["provider_slug"], "provider_name": row["provider_name"],
                    "model_id": row["model_id"], "power": row["power"],
                    "quota_weight": row["quota_weight"],
                    "capabilities": json.loads(row["capabilities_json"]),
                    "metadata_confidence": row["metadata_confidence"],
                    "state": row["state"], "last_seen": row["last_seen"],
                }
                for row in coordinator.store.discovered_model_rows()
            ],
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
            if self.path == "/status":
                self._send(200, self._operational_status())
                return
            if self.path == "/admin/config":
                self._send(200, config_snapshot())
                return
            parsed_path = urlsplit(self.path)
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
                result = discover_models(endpoint, api_key)
                models = [classify_model(model) for model in result.models]
                if result.success:
                    coordinator.store.save_discovered_models(provider, models, now=time.time())
                self._send(200, {"success": result.success, "models": models, "endpoint": result.endpoint, "error": result.error, "persisted": len(models) if result.success else 0})
                return
            if self.path == "/admin":
                self._send_html(200, """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>aipool / provider console</title><style>
:root{color-scheme:dark;--bg:#101414;--panel:#182020;--panel2:#202b2a;--ink:#e9f0e9;--muted:#9eafaa;--line:#33423f;--accent:#c5f36b;--warn:#ffcf70;--bad:#ff8f86}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% -10%,#2d463a 0,#101414 42%);color:var(--ink);font:16px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}main{max-width:1120px;margin:0 auto;padding:52px 24px 80px}header{display:flex;justify-content:space-between;gap:24px;align-items:end;border-bottom:1px solid var(--line);padding-bottom:28px;margin-bottom:30px}h1{font:800 clamp(2rem,5vw,4.5rem)/.95 Georgia,serif;letter-spacing:-.06em;margin:0;max-width:650px}h1 span{color:var(--accent)}h2{font-size:1rem;letter-spacing:.08em;text-transform:uppercase;color:var(--accent);margin:34px 0 14px}.lede{color:var(--muted);max-width:720px}.signal{color:var(--warn);font-size:.8rem;text-align:right}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:14px}.card{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 12px 32px #0003}.card header{border:0;padding:0;margin:0 0 16px;align-items:start}.card h3{margin:0;font-size:1rem}.tag{display:inline-block;border:1px solid #536558;border-radius:99px;color:var(--accent);font-size:.7rem;padding:2px 8px;margin-top:5px}.meta{color:var(--muted);font-size:.75rem;margin:10px 0 16px}.meta a{color:var(--accent)}label{display:block;color:var(--muted);font-size:.75rem;margin:12px 0 5px}input{width:100%;background:#0d1212;border:1px solid var(--line);border-radius:7px;color:var(--ink);padding:10px;font:inherit;font-size:.85rem}input:focus,button:focus{outline:2px solid var(--accent);outline-offset:2px}.toggle{display:flex;gap:9px;align-items:center;color:var(--ink)}.toggle input{width:auto;accent-color:var(--accent)}button{border:0;border-radius:8px;background:var(--accent);color:#111a13;padding:12px 18px;font:800 .85rem ui-monospace;cursor:pointer}.actions{display:flex;align-items:center;gap:16px;margin-top:24px}.status{color:var(--muted);font-size:.8rem}.advanced{background:#0d1212;border:1px solid var(--line);padding:18px;border-radius:12px}.advanced .grid{grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}small{color:var(--muted)}@media(max-width:560px){main{padding:28px 14px}header{display:block}.signal{text-align:left;margin-top:16px}}
</style></head><body><main><header><div><h1>provider<br><span>console</span></h1><p class="lede">Configure free-tier compute by model, not by brand. Every card remains quarantined until its smoke test proves capability and quota economics.</p></div><div class="signal">LOCAL OPERATOR PANEL<br>SECRETS NEVER ECHOED</div></header><form id="f"><section><h2>Model pool</h2><div id="providers" class="grid"><p class="status">Loading catalog…</p></div></section><section><h2>Advanced bridges</h2><div class="advanced"><div class="grid"><div><label for="hfmodel">Legacy HF model</label><input id="hfmodel" name="AIPOOL_HF_MODEL" placeholder="Use a model card above"></div><div><label for="hftoken">HF token</label><input id="hftoken" name="HF_TOKEN" type="password" autocomplete="new-password" placeholder="Leave blank to keep current"></div><div><label for="endpoint">Custom OpenAI-compatible endpoint</label><input id="endpoint" name="AIPOOL_OPENAI_ENDPOINT"></div><div><label for="openmodel">Custom model</label><input id="openmodel" name="AIPOOL_OPENAI_MODEL"></div></div></div></section><div class="actions"><button type="submit">Save configuration</button><span id="o" class="status" role="status"></span></div></form></main><script>
const form=document.querySelector('#f'),cards=document.querySelector('#providers'),out=document.querySelector('#o');
const key=(slug,suffix)=>'AIPOOL_MODEL_'+slug.toUpperCase().replaceAll('-','_')+'_'+suffix;
const providerKey=(slug,suffix)=>'AIPOOL_PROVIDER_'+slug.toUpperCase().replaceAll('-','_')+'_'+suffix;
function card(p){let keyName=providerKey(p.provider_slug,'API_KEY');return `<article class="card"><header><div><h3>${esc(p.name)}</h3><span class="tag">${esc(p.power)} · quota ×${p.quota_weight}</span></div><label class="toggle"><input type="checkbox" data-key="${key(p.slug,'ENABLED')}" ${p.enabled?'checked':''}> enable</label></header><p class="meta"><a href="${esc(p.source_url)}" target="_blank" rel="noreferrer">source</a> · ${esc(p.transport)} · ${p.adapter==='manual'?'adapter needed':'OpenAI-compatible'}<br>provider key is shared across this model family<br>default model: ${esc(p.model)}</p><label>Model ID<input data-key="${key(p.slug,'MODEL')}" value="${esc(p.configured_model)}"></label><label>API key ${p.has_api_key?'(saved; leave blank to preserve)':''}<input type="password" autocomplete="new-password" data-key="${keyName}" placeholder="one key for ${esc(p.provider_slug)}"></label><button type="button" onclick="refreshModels('${p.slug}')">Refresh live model list</button><span class="status" id="live-${p.slug}"></span></article>`}
function esc(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function refreshModels(slug){let node=document.querySelector('#live-'+slug);node.textContent=' checking…';let r=await fetch('/admin/discover-models?slug='+encodeURIComponent(slug));let d=await r.json();node.textContent=d.success?' live '+d.models.length+' models: '+d.models.slice(0,5).map(m=>m.id+' ['+m.power+']').join(', '):( ' '+(d.error||'unavailable'));}
async function load(){let r=await fetch('/admin/config');let c=await r.json();document.querySelector('#hfmodel').value=c.settings.AIPOOL_HF_MODEL||'';document.querySelector('#endpoint').value=c.settings.AIPOOL_OPENAI_ENDPOINT||'';document.querySelector('#openmodel').value=c.settings.AIPOOL_OPENAI_MODEL||'';let groups={};for(let p of c.providers)(groups[p.provider_slug]??={name:p.provider_name,items:[]}).items.push(p);let catalog=Object.values(groups).map(g=>`<section><h3>${esc(g.name)}</h3><div class="grid">${g.items.map(card).join('')}</div></section>`).join('');let findings=c.discovered_models?.length?`<section><h3>Quarantined live findings</h3><div class="grid">${c.discovered_models.slice(0,96).map(m=>`<article class="card"><h3>${esc(m.model_id)}</h3><span class="tag">${esc(m.power)} · quota ×${m.quota_weight} · ${esc(m.metadata_confidence)} confidence</span><p class="meta">${esc(m.provider_name)} · ${esc(m.state)} · capabilities: ${esc(m.capabilities.join(', '))}</p></article>`).join('')}</div></section>`:'';cards.innerHTML=(catalog||'<p class="status">No API models in the catalog.</p>')+findings}
form.onsubmit=async e=>{e.preventDefault();let payload={};for(let el of form.querySelectorAll('[data-key],input[name]')){let k=el.dataset.key||el.name;if(el.type==='password'&&!el.value)continue;payload[k]=el.type==='checkbox'?(el.checked?'1':'0'):el.value}let r=await fetch('/admin/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});let data=await r.json();out.textContent=data.updated?'Saved. Restart required before routing changes apply.':(data.error||'Save failed')};load();
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
