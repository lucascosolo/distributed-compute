"""Compact command-line interface for agent callers."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
from dataclasses import replace
from dataclasses import asdict
from pathlib import Path

from .client import cancel_remote, enqueue_remote, get_remote_capabilities, get_remote_queue, RemoteCoordinatorError, submit_remote, upload_artifact_remote
from .artifacts import ArtifactStore
from .benchmark import default_cases, run_benchmark
from .comparison import run_comparison
from .context import ContextPacket
from .capabilities import capability_document
from .discovery import CandidateRegistry, CommandCandidateProbe, QuarantineProbePipeline, promote_lead
from .discovery_sources import DiscoveryRunner, HtmlPageSource, LeadRegistry, LocalCatalogSource, RedditSearchSource, RedditThreadSource
from .discovered import build_discovered_adapter
from .model_discovery import classify_model
from .domain import ProviderProfile, ProviderState, TaskEnvelope
from .gateway import make_server
from .queue import QueueFull, TaskQueue, record_to_dict
from .providers import AgentCommandAdapter, BrowserCommandAdapter, CandidateCommandAdapter, CloudflareWorkersAIAdapter, CommandAdapter, FixtureAdapter, HuggingFaceInferenceAdapter, OpenAICompatibleAdapter, ProviderRegistry, TokenRouterResponsesAdapter
from .provider_catalog import config_prefix, load_catalog, model_config_prefix, provider_config_name
from .service import Coordinator
from .storage import Store
from .worker import QueueWorker


def _load_local_config() -> None:
    """Load operator config, allowing the dashboard file to override stale process values."""
    candidates: list[Path] = []
    configured = os.environ.get("AIPOOL_CONFIG_FILE")
    if configured:
        candidates.append(Path(configured).expanduser())
    else:
        local_config = Path.cwd() / ".aipool.local"
        candidates.append(local_config)
        # A repository-local config is an explicit local development boundary;
        # do not let shared remote settings silently switch its default mode.
        if local_config.is_file() and "AIPOOL_MODE" not in os.environ:
            os.environ["AIPOOL_MODE"] = "local"
        # A session's origin, when already supplied by its launcher, selects
        # the matching per-agent file. Otherwise retain the historical Claude
        # then Codex discovery order, while always merging shared settings.
        origin = os.environ.get("AIPOOL_ORIGIN_PROVIDER_ID", "")
        if origin == "agent:codex":
            candidates.append(Path.home() / ".codex" / "distributed-compute.env")
        elif origin == "agent:claude":
            candidates.append(Path.home() / ".claude" / "distributed-compute.env")
        else:
            candidates.extend((
                Path.home() / ".claude" / "distributed-compute.env",
                Path.home() / ".codex" / "distributed-compute.env",
            ))
        candidates.append(Path.home() / ".agents" / "distributed-compute.env")
    for path in candidates:
        if not path.is_file():
            continue
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip("'\"")
            if key.startswith("AIPOOL_") or key in {"HF_TOKEN"}:
                # A configured operator file is the live control plane for the
                # dashboard. Its model toggles and rotated credentials must take
                # effect on reload even when an older EnvironmentFile value is
                # still present in the long-running process.
                if configured:
                    os.environ[key] = value
                else:
                    os.environ.setdefault(key, value)


def _nonnegative_int(value: str | None, default: int = 0) -> int:
    try:
        return max(0, int(value)) if value else default
    except (TypeError, ValueError):
        return default


def _positive_float(value: str | None, default: float) -> float:
    try:
        parsed = float(value) if value else default
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def _apply_origin(task: TaskEnvelope) -> TaskEnvelope:
    """Stamp top-level native callers without allowing them to overwrite a hop."""
    origin = os.environ.get("AIPOOL_ORIGIN_PROVIDER_ID", "").strip()
    if not origin or task.origin_provider_id:
        return task
    chain = task.delegation_chain if origin in task.delegation_chain else (*task.delegation_chain, origin)
    return replace(task, origin_provider_id=origin, delegation_chain=chain)


def _cloudflare_access_headers() -> dict[str, str]:
    """Return optional Access service-token headers without exposing their values."""
    client_id = os.environ.get("AIPOOL_CF_ACCESS_CLIENT_ID", "")
    client_secret = os.environ.get("AIPOOL_CF_ACCESS_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return {}
    return {"CF-Access-Client-Id": client_id, "CF-Access-Client-Secret": client_secret}


def _remote_timeout_seconds() -> float:
    """Allow slow shared providers to finish before the caller gives up."""
    return _positive_float(os.environ.get("AIPOOL_REMOTE_TIMEOUT_SECONDS"), 180.0)


def _baseline_command(command: tuple[str, ...], timeout: float):
    def run(packet: ContextPacket) -> str:
        completed = subprocess.run(
            command, input=packet.render().encode(), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout, check=False, shell=False,
        )
        if completed.returncode:
            raise RuntimeError("baseline command failed")
        if len(completed.stdout) > 1_000_000:
            raise RuntimeError("baseline output exceeds limit")
        return completed.stdout.decode(errors="replace")
    return run


def _build_registry(args: argparse.Namespace, store: Store | None = None) -> ProviderRegistry:
    registry = ProviderRegistry()
    artifact_root = os.environ.get("AIPOOL_ARTIFACT_ROOT")
    if not artifact_root and os.environ.get("AIPOOL_CONFIG_FILE"):
        artifact_root = str(Path(os.environ["AIPOOL_CONFIG_FILE"]).expanduser().parent / "artifacts")
    artifact_store = ArtifactStore(artifact_root or ".aipool-artifacts")
    fixture_output = os.environ.get("AIPOOL_FIXTURE_OUTPUT")
    if fixture_output is not None:
        profile = ProviderProfile(
            "fixture", "Configured fixture", "fixture",
            capabilities={"classification": 0.8, "structured_json": 0.8, "extraction": 0.8, "summarization": 0.6},
            reliability=0.5, state=ProviderState.HEALTHY, max_complexity=1,
        )
        registry.register(FixtureAdapter(profile, lambda _: fixture_output))
    command = os.environ.get("AIPOOL_COMMAND")
    if command:
        profile = ProviderProfile(
            "local-command", "Configured local command", "command",
            capabilities={"classification": 0.7, "structured_json": 0.7, "extraction": 0.7, "summarization": 0.5},
            reliability=0.5, state=ProviderState.HEALTHY, max_complexity=2,
        )
        registry.register(CommandAdapter(profile, tuple(shlex.split(command))))
    for runtime, label in (("claude", "Claude CLI"), ("codex", "Codex CLI")):
        agent_command = os.environ.get(f"AIPOOL_{runtime.upper()}_COMMAND")
        if not agent_command:
            continue
        profile = ProviderProfile(
            f"agent:{runtime}", label, "agent-command",
            capabilities={"classification": 0.9, "structured_json": 0.9,
                          "extraction": 0.9, "summarization": 0.9,
                          "coding": 0.9, "code_review": 0.9,
                          "reasoning": 0.9, "instruction_following": 0.9,
                          "research": 0.8, "long_context": 0.9},
            reliability=0.7, state=ProviderState.HEALTHY, max_complexity=5,
        )
        registry.register(AgentCommandAdapter(profile, tuple(shlex.split(agent_command)), artifacts=artifact_store))
    endpoint = os.environ.get("AIPOOL_OPENAI_ENDPOINT")
    if endpoint and os.environ.get("AIPOOL_OPENAI_MODEL"):
        profile = ProviderProfile(
            "openai-compatible", "Configured OpenAI-compatible provider", "openai-compatible",
            capabilities={"classification": 0.8, "structured_json": 0.8, "extraction": 0.8, "summarization": 0.8, "coding": 0.7, "instruction_following": 0.8},
            reliability=0.5, state=ProviderState.HEALTHY, max_complexity=4,
        )
        registry.register(OpenAICompatibleAdapter(profile, endpoint, os.environ["AIPOOL_OPENAI_MODEL"], "AIPOOL_OPENAI_API_KEY", artifacts=artifact_store))
    omniroute_endpoint = os.environ.get("AIPOOL_OMNIROUTE_ENDPOINT")
    omniroute_model = os.environ.get("AIPOOL_OMNIROUTE_MODEL")
    omniroute_enabled = os.environ.get("AIPOOL_OMNIROUTE_ENABLED", "").casefold() in {"1", "true", "yes", "on"}
    if omniroute_enabled and omniroute_endpoint and omniroute_model:
        power = os.environ.get("AIPOOL_OMNIROUTE_POWER", "strong").casefold()
        max_complexity = 1 if power == "light" else 2 if power == "medium" else 3 if power == "strong" else 4
        profile = ProviderProfile(
            f"omniroute:{omniroute_model}", "OmniRoute aggregate gateway", "omniroute",
            capabilities={
                "classification": 0.8, "structured_json": 0.8,
                "extraction": 0.8, "summarization": 0.8,
                "coding": 0.8, "code_review": 0.8,
                "reasoning": 0.8, "instruction_following": 0.8,
                "research": 0.7, "long_context": 0.8,
            },
            reliability=0.5, state=ProviderState.HEALTHY,
            max_complexity=max_complexity,
            request_limit=_nonnegative_int(os.environ.get("AIPOOL_OMNIROUTE_REQUEST_LIMIT")),
            token_limit=_nonnegative_int(os.environ.get("AIPOOL_OMNIROUTE_TOKEN_LIMIT")),
            usage_window_seconds=_positive_float(os.environ.get("AIPOOL_OMNIROUTE_USAGE_WINDOW_SECONDS"), 60.0),
            quota_group="omniroute",
        )
        endpoint = omniroute_endpoint.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        registry.register(OpenAICompatibleAdapter(
            profile, endpoint, omniroute_model, "AIPOOL_OMNIROUTE_API_KEY",
            api_key_file=os.environ.get("AIPOOL_OMNIROUTE_API_KEY_FILE", ""),
            model_by_task={
                task_kind: model
                for task_kind, model in (
                    ("coding", os.environ.get("AIPOOL_OMNIROUTE_MODEL_CODING", "")),
                    ("code_review", os.environ.get("AIPOOL_OMNIROUTE_MODEL_CODE_REVIEW", "")),
                    ("reasoning", os.environ.get("AIPOOL_OMNIROUTE_MODEL_REASONING", "")),
                    ("planning", os.environ.get("AIPOOL_OMNIROUTE_MODEL_REASONING", "")),
                )
                if model
            },
            artifacts=artifact_store,
        ))
        selected_models = tuple(dict.fromkeys(
            model.strip() for model in os.environ.get("AIPOOL_OMNIROUTE_MODELS", "").split(",")
            if model.strip() and model.strip() != omniroute_model
        ))
        for selected_model in selected_models:
            metadata = classify_model(selected_model)
            selected_power = str(metadata["power"])
            selected_complexity = 1 if selected_power == "light" else 2 if selected_power == "medium" else 3 if selected_power == "strong" else 4
            selected_capabilities = {
                "classification": 0.6, "structured_json": 0.6,
                "extraction": 0.6, "summarization": 0.6,
            }
            specialized_score = 0.8 if selected_complexity >= 4 else 0.7
            for capability in metadata["capabilities"]:
                selected_capabilities[str(capability)] = specialized_score
            selected_profile = ProviderProfile(
                f"omniroute:{selected_model}", f"OmniRoute · {selected_model}", "omniroute",
                capabilities=selected_capabilities, reliability=0.3,
                state=ProviderState.QUARANTINED, max_complexity=selected_complexity,
                quota_weight=float(metadata["quota_weight"]),
                request_limit=_nonnegative_int(os.environ.get("AIPOOL_OMNIROUTE_REQUEST_LIMIT")),
                token_limit=_nonnegative_int(os.environ.get("AIPOOL_OMNIROUTE_TOKEN_LIMIT")),
                usage_window_seconds=_positive_float(os.environ.get("AIPOOL_OMNIROUTE_USAGE_WINDOW_SECONDS"), 60.0),
                quota_group="omniroute",
            )
            registry.register(OpenAICompatibleAdapter(
                selected_profile, endpoint, selected_model, "AIPOOL_OMNIROUTE_API_KEY",
                api_key_file=os.environ.get("AIPOOL_OMNIROUTE_API_KEY_FILE", ""),
                artifacts=artifact_store,
            ))
    hf_model = os.environ.get("AIPOOL_HF_MODEL")
    if hf_model:
        profile = ProviderProfile(
            "huggingface-inference", "Hugging Face Inference Providers", "huggingface-api",
            capabilities={"classification": 0.8, "structured_json": 0.8, "extraction": 0.8,
                          "summarization": 0.8, "coding": 0.7, "instruction_following": 0.8},
            reliability=0.5, state=ProviderState.HEALTHY, max_complexity=4,
        )
        registry.register(HuggingFaceInferenceAdapter(
            profile, hf_model, endpoint=os.environ.get(
                "AIPOOL_HF_ENDPOINT", "https://router.huggingface.co/v1/chat/completions"
            ), artifacts=artifact_store,
        ))
    ollama_model = os.environ.get("AIPOOL_OLLAMA_MODEL")
    if ollama_model:
        ollama_power = os.environ.get("AIPOOL_OLLAMA_POWER", "medium").casefold()
        ollama_complexity = 1 if ollama_power == "light" else 2 if ollama_power == "medium" else 3 if ollama_power == "strong" else 4
        capabilities = {
            "classification": 0.7, "structured_json": 0.7,
            "extraction": 0.7, "summarization": 0.7,
        }
        if ollama_complexity >= 3:
            capabilities.update({"coding": 0.7, "instruction_following": 0.7})
        registry.register(OpenAICompatibleAdapter(
            ProviderProfile(
                "ollama-local", "Ollama (local)", "ollama",
                capabilities=capabilities, reliability=0.5,
                state=ProviderState.HEALTHY, max_complexity=ollama_complexity,
                quota_group="ollama-local",
            ),
            os.environ.get("AIPOOL_OLLAMA_ENDPOINT", "http://127.0.0.1:11434/v1/chat/completions"),
            ollama_model, "", static_api_key="ollama",
            artifacts=artifact_store,
        ))
    for catalog_provider in load_catalog():
        provider_prefix = config_prefix(catalog_provider)
        model_prefix = model_config_prefix(catalog_provider)
        enabled_setting = os.environ.get(f"{model_prefix}_ENABLED", "")
        family_key_present = bool(os.environ.get(f"{provider_prefix}_API_KEY"))
        if not family_key_present and catalog_provider.transport == "huggingface-api":
            family_key_present = bool(os.environ.get("HF_TOKEN"))
        if enabled_setting and enabled_setting.casefold() not in {"1", "true", "yes", "on"}:
            continue
        if not enabled_setting and not family_key_present and not catalog_provider.api_key_optional:
            continue
        if any(not os.environ.get(provider_config_name(catalog_provider, field)) for field in catalog_provider.required_config):
            continue
        api_key_env = f"{provider_prefix}_API_KEY"
        if not os.environ.get(api_key_env) and catalog_provider.transport == "huggingface-api":
            api_key_env = "HF_TOKEN"
        if not os.environ.get(api_key_env) and not catalog_provider.api_key_optional:
            continue
        model = os.environ.get(f"{model_prefix}_MODEL") or catalog_provider.model
        request_limit = _nonnegative_int(os.environ.get(f"{provider_prefix}_REQUEST_LIMIT"))
        token_limit = _nonnegative_int(os.environ.get(f"{provider_prefix}_TOKEN_LIMIT"))
        usage_window_seconds = _positive_float(
            os.environ.get(f"{provider_prefix}_USAGE_WINDOW_SECONDS"), 60.0
        )
        timeout_seconds = _positive_float(
            os.environ.get(f"{provider_prefix}_TIMEOUT_SECONDS"), 120.0
        )
        power = catalog_provider.power.casefold()
        max_complexity = 1 if power == "light" else 2 if power == "medium" else 3 if power == "strong" else 4
        capabilities = {"classification": 0.6, "structured_json": 0.6, "extraction": 0.6, "summarization": 0.6}
        if max_complexity >= 3:
            capabilities.update({"coding": 0.7, "instruction_following": 0.7})
        profile = ProviderProfile(
            f"catalog:{catalog_provider.slug}", catalog_provider.name, catalog_provider.transport,
            capabilities=capabilities, reliability=0.2, state=ProviderState.QUARANTINED,
            max_complexity=max_complexity, quota_weight=catalog_provider.quota_weight,
            request_limit=request_limit, token_limit=token_limit,
            usage_window_seconds=usage_window_seconds,
            quota_group=f"catalog:{catalog_provider.provider_slug}",
        )
        if catalog_provider.transport == "openai-compatible":
            endpoint = os.environ.get(f"{provider_prefix}_ENDPOINT") or catalog_provider.endpoint
            if not endpoint.rstrip("/").endswith("/chat/completions"):
                endpoint = endpoint.rstrip("/") + "/chat/completions"
            headers_extra = {"X-Free-Fallback": "false"} if catalog_provider.provider_slug == "bazaarlink" else {}
            registry.register(OpenAICompatibleAdapter(
                profile, endpoint, model, api_key_env,
                headers_extra=headers_extra, allow_anonymous=catalog_provider.api_key_optional,
                timeout_seconds=timeout_seconds,
                artifacts=artifact_store,
            ))
        elif catalog_provider.transport == "cloudflare-workers-ai":
            registry.register(CloudflareWorkersAIAdapter(
                profile, model, api_key_env,
                provider_config_name(catalog_provider, "account_id"),
                endpoint=os.environ.get(f"{provider_prefix}_ENDPOINT") or catalog_provider.endpoint,
                timeout_seconds=timeout_seconds,
            ))
        elif catalog_provider.transport == "tokenrouter-responses":
            registry.register(TokenRouterResponsesAdapter(
                profile, model, api_key_env,
                endpoint=os.environ.get(f"{provider_prefix}_ENDPOINT") or catalog_provider.endpoint,
                timeout_seconds=timeout_seconds,
            ))
        elif catalog_provider.transport == "huggingface-api":
            registry.register(HuggingFaceInferenceAdapter(
                profile, model, api_key_env, catalog_provider.endpoint,
                timeout_seconds=timeout_seconds,
            ))
    if store is not None:
        catalog_by_family = {provider.provider_slug: provider for provider in load_catalog()}
        for row in store.discovered_model_rows():
            if row["state"] != "active":
                continue
            catalog_provider = catalog_by_family.get(str(row["provider_slug"]))
            if catalog_provider is None:
                continue
            provider_prefix = config_prefix(catalog_provider)
            api_key_env = f"{provider_prefix}_API_KEY"
            if not os.environ.get(api_key_env) and row["transport"] == "huggingface-api":
                api_key_env = "HF_TOKEN"
            try:
                adapter = build_discovered_adapter(
                    row, api_key_env=api_key_env,
                    request_limit=_nonnegative_int(os.environ.get(f"{provider_prefix}_REQUEST_LIMIT")),
                    token_limit=_nonnegative_int(os.environ.get(f"{provider_prefix}_TOKEN_LIMIT")),
                    usage_window_seconds=_positive_float(os.environ.get(f"{provider_prefix}_USAGE_WINDOW_SECONDS"), 60.0),
                    state=ProviderState.HEALTHY,
                )
                registry.register(adapter)
            except (TypeError, ValueError, KeyError):
                continue
    browser_command = os.environ.get("AIPOOL_BROWSER_COMMAND")
    if browser_command:
        profile = ProviderProfile(
            "browser-chat", "Configured browser chat", "browser-chat",
            capabilities={"classification": 0.7, "structured_json": 0.6,
                           "extraction": 0.7, "summarization": 0.6},
            reliability=0.4, state=ProviderState.HEALTHY, max_complexity=2,
        )
        registry.register(BrowserCommandAdapter(
            profile,
            tuple(shlex.split(browser_command)),
            artifact_store,
        ))
    return registry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aipool")
    subparsers = parser.add_subparsers(dest="command", required=True)
    task = subparsers.add_parser("task", help="submit one compact task envelope")
    task.add_argument("--json", required=True, dest="task_json")
    task.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    artifact = subparsers.add_parser("artifact", help="upload bounded task context")
    artifact_subparsers = artifact.add_subparsers(dest="artifact_action", required=True)
    upload = artifact_subparsers.add_parser("upload", help="upload one bounded file or stdin payload")
    upload.add_argument("--file", default="-", help="file to upload, or - for stdin")
    subparsers.add_parser("providers", help="list configured providers")
    discover = subparsers.add_parser("discover", help="collect bounded public chatbot discovery leads")
    discover_input = discover.add_mutually_exclusive_group(required=True)
    discover_input.add_argument("--query")
    discover_input.add_argument("--thread-url")
    discover_input.add_argument("--page-url")
    discover_input.add_argument("--catalog-file")
    discover.add_argument("--subreddit")
    discover.add_argument("--max-results", type=int, default=10)
    discover.add_argument("--max-leads", type=int, default=32)
    discover.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    candidate = subparsers.add_parser("candidate", help="review and promote a discovered lead")
    candidate_subparsers = candidate.add_subparsers(dest="candidate_action", required=True)
    promote = candidate_subparsers.add_parser("promote", help="put one lead into provider quarantine")
    promote.add_argument("lead_id")
    promote.add_argument("--terms-review", required=True)
    promote.add_argument("--terms-prohibited", action="store_true")
    promote.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    probe = candidate_subparsers.add_parser("probe", help="run bounded probes for quarantined candidates")
    probe.add_argument("--probe-command", dest="probe_command", default=os.environ.get("AIPOOL_CANDIDATE_PROBE_COMMAND"))
    probe.add_argument("--max-candidates", type=int, default=3)
    probe.add_argument("--timeout", type=float, default=120.0)
    probe.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    activate = candidate_subparsers.add_parser("activate", help="explicitly approve a successfully probed candidate")
    activate.add_argument("candidate_id")
    activate.add_argument("--operator-approved", action="store_true")
    activate.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    listing = candidate_subparsers.add_parser("list", help="list discovered candidates and evidence state")
    listing.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    benchmark = candidate_subparsers.add_parser("benchmark", help="run capability cases against an approved candidate")
    benchmark.add_argument("candidate_id")
    benchmark.add_argument("--command", dest="candidate_command", default=os.environ.get("AIPOOL_CANDIDATE_COMMAND"))
    benchmark.add_argument("--timeout", type=float, default=120.0)
    benchmark.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    subparsers.add_parser("status", help="show coordinator status")
    subparsers.add_parser("capabilities", help="describe bounded work classes and task envelope contract")
    stats = subparsers.add_parser("stats", help="show delegation economics and provider usage")
    stats.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    compare = subparsers.add_parser("compare", help="compare bounded native baseline work with distributed work")
    compare.add_argument("--baseline-command", default=os.environ.get("AIPOOL_BASELINE_COMMAND"), required=not bool(os.environ.get("AIPOOL_BASELINE_COMMAND")))
    compare.add_argument("--local-estimate", type=float, default=1.0)
    compare.add_argument("--timeout", type=float, default=120.0)
    compare.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    serve = subparsers.add_parser("serve", help="run the local or authorized remote gateway")
    serve.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    serve.add_argument("--host", default=os.environ.get("AIPOOL_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("AIPOOL_PORT", "8765")))
    serve.add_argument("--no-worker", action="store_true", help="serve HTTP without processing queued tasks")
    queue = subparsers.add_parser("queue", help="enqueue, inspect, or cancel durable tasks")
    queue_subparsers = queue.add_subparsers(dest="queue_action", required=True)
    queue_submit = queue_subparsers.add_parser("submit", help="enqueue one task without waiting for completion")
    queue_submit.add_argument("--json", required=True, dest="task_json")
    queue_submit.add_argument("--idempotency-key")
    queue_submit.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    queue_status = queue_subparsers.add_parser("status", help="show one queued task")
    queue_status.add_argument("task_id")
    queue_status.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    queue_cancel = queue_subparsers.add_parser("cancel", help="cancel one queued or running task")
    queue_cancel.add_argument("task_id")
    queue_cancel.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    return parser


def main(argv: list[str] | None = None) -> int:
    _load_local_config()
    args = _parser().parse_args(argv)
    registry = _build_registry(args)
    if args.command == "capabilities":
        if os.environ.get("AIPOOL_MODE", "local").lower() == "remote":
            try:
                result = get_remote_capabilities(
                    os.environ.get("AIPOOL_BASE_URL", ""),
                    token=os.environ.get("AIPOOL_TOKEN") or None,
                    headers_extra=_cloudflare_access_headers(),
                    timeout_seconds=_remote_timeout_seconds(),
                )
            except RemoteCoordinatorError as exc:
                print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
                return 1
        else:
            result = capability_document(adapter.profile for adapter in registry.all())
        print(json.dumps(result, separators=(",", ":")))
        return 0
    if args.command == "discover":
        store = Store(args.db)
        try:
            if args.thread_url:
                source = RedditThreadSource(args.thread_url, max_results=args.max_results)
            elif args.page_url:
                source = HtmlPageSource(args.page_url, max_results=args.max_results)
            elif args.catalog_file:
                source = LocalCatalogSource(args.catalog_file, max_results=args.max_results)
            else:
                source = RedditSearchSource(args.query, subreddit=args.subreddit, max_results=args.max_results)
            result = DiscoveryRunner((source,), max_leads=args.max_leads).run(LeadRegistry(store))
            print(json.dumps({
                "leads": [lead.to_dict() for lead in result.leads],
                "errors": list(result.errors),
            }, separators=(",", ":")))
            return 0 if not result.errors else 1
        except (ValueError, TypeError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
            return 2
        finally:
            store.close()
    if args.command == "candidate" and args.candidate_action == "promote":
        store = Store(args.db)
        try:
            lead = LeadRegistry(store).get(args.lead_id)
            candidate = promote_lead(
                CandidateRegistry(store), lead,
                terms_review=args.terms_review,
                terms_prohibited=args.terms_prohibited,
            )
            print(json.dumps({
                "id": candidate.id, "name": candidate.name,
                "state": candidate.state.value, "endpoint": candidate.endpoint,
                "rejection_reason": candidate.rejection_reason,
            }, separators=(",", ":")))
            return 0
        except (KeyError, ValueError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
            return 2
        finally:
            store.close()
    if args.command == "candidate" and args.candidate_action == "probe":
        store = Store(args.db)
        try:
            if not args.probe_command:
                print(json.dumps({"success": False, "error": "candidate probe command is not configured"}, separators=(",", ":")))
                return 2
            pipeline = QuarantineProbePipeline(
                CandidateRegistry(store),
                CommandCandidateProbe(tuple(shlex.split(args.probe_command)), timeout_seconds=args.timeout),
                max_candidates=args.max_candidates,
            )
            reports = pipeline.run()
            print(json.dumps({"reports": [json.loads(report.to_json()) for report in reports]}, separators=(",", ":")))
            return 0
        except (ValueError, TypeError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
            return 2
        finally:
            store.close()
    if args.command == "candidate" and args.candidate_action == "activate":
        store = Store(args.db)
        try:
            candidate = CandidateRegistry(store).activate(
                args.candidate_id, operator_approved=args.operator_approved,
            )
            print(json.dumps({
                "id": candidate.id, "state": candidate.state.value,
                "endpoint": candidate.endpoint,
            }, separators=(",", ":")))
            return 0
        except (KeyError, ValueError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
            return 2
        finally:
            store.close()
    if args.command == "candidate" and args.candidate_action == "list":
        store = Store(args.db)
        try:
            registry = CandidateRegistry(store)
            print(json.dumps({"candidates": [
                {
                    "id": candidate.id,
                    "name": candidate.name,
                    "transport": candidate.transport,
                    "endpoint": candidate.endpoint,
                    "state": candidate.state.value,
                    "probe_passed": bool(registry.probe_result(candidate.id) and registry.probe_result(candidate.id).passed),
                }
                for candidate in registry.all()
            ]}, separators=(",", ":")))
            return 0
        finally:
            store.close()
    if args.command == "candidate" and args.candidate_action == "benchmark":
        store = Store(args.db)
        try:
            registry = CandidateRegistry(store)
            candidate = registry.get(args.candidate_id)
            if candidate.state.value != "approved":
                raise ValueError("candidate must be explicitly approved before benchmarking")
            probe = registry.probe_result(candidate.id)
            if probe is None or not probe.passed:
                raise ValueError("successful candidate probe is required before benchmarking")
            if not args.candidate_command:
                raise ValueError("candidate benchmark command is not configured")
            profile = ProviderProfile(
                candidate.id, candidate.name, candidate.transport,
                reliability=0.5, state=ProviderState.HEALTHY, max_complexity=2,
            )
            result = run_benchmark(CandidateCommandAdapter(
                profile, asdict(candidate), tuple(shlex.split(args.candidate_command)),
                timeout_seconds=args.timeout,
            ))
            store.record_benchmark(result)
            print(json.dumps({
                "provider_id": result.provider_id, "scores": result.scores,
                "attempts": result.attempts, "valid": result.valid,
            }, separators=(",", ":")))
            return 0
        except (KeyError, ValueError, TypeError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
            return 2
        finally:
            store.close()
    if args.command == "queue":
        mode = os.environ.get("AIPOOL_MODE", "local").lower()
        base_url = os.environ.get("AIPOOL_BASE_URL", "")
        token = os.environ.get("AIPOOL_TOKEN") or None
        if args.queue_action == "submit":
            try:
                task = _apply_origin(TaskEnvelope.from_dict(json.loads(args.task_json)))
            except (ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
                print(json.dumps({"success": False, "error": f"invalid task envelope: {exc}"}, separators=(",", ":")))
                return 2
            try:
                if mode == "remote":
                    result = enqueue_remote(
                        base_url, task, token=token, idempotency_key=args.idempotency_key,
                        headers_extra=_cloudflare_access_headers(),
                        timeout_seconds=_remote_timeout_seconds(),
                    )
                else:
                    store = Store(args.db)
                    try:
                        result = record_to_dict(TaskQueue(store, max_pending=int(os.environ.get("AIPOOL_MAX_PENDING", "1000"))).enqueue(task, idempotency_key=args.idempotency_key))
                    finally:
                        store.close()
            except (QueueFull, RemoteCoordinatorError, ValueError) as exc:
                print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
                return 1
            print(json.dumps(result, separators=(",", ":")))
            return 0
        try:
            if mode == "remote":
                operation = get_remote_queue if args.queue_action == "status" else cancel_remote
                result = operation(
                    base_url, args.task_id, token=token,
                    headers_extra=_cloudflare_access_headers(),
                    timeout_seconds=_remote_timeout_seconds(),
                )
            else:
                store = Store(args.db)
                try:
                    queue = TaskQueue(store)
                    if args.queue_action == "status":
                        record = queue.get(args.task_id)
                        if record is None:
                            print(json.dumps({"error": "queue task not found"}, separators=(",", ":")))
                            return 1
                    else:
                        queue.cancel(args.task_id)
                        record = queue.get(args.task_id)
                        if record is None:
                            print(json.dumps({"error": "queue task not found"}, separators=(",", ":")))
                            return 1
                    result = record_to_dict(record)
                finally:
                    store.close()
        except RemoteCoordinatorError as exc:
            print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
            return 1
        print(json.dumps(result, separators=(",", ":")))
        return 0
    if args.command == "compare":
        store = Store(args.db)
        try:
            if args.local_estimate <= 0 or args.timeout <= 0:
                raise ValueError("local estimate and timeout must be positive")
            command = tuple(shlex.split(args.baseline_command))
            if not command:
                raise ValueError("baseline command is required")
            cases = tuple(
                replace(case, task=replace(case.task, local_estimate=args.local_estimate))
                for case in default_cases()
            )
            report = run_comparison(
                cases, _baseline_command(command, args.timeout),
                Coordinator(_build_registry(args, store), store),
            )
            print(json.dumps(report.to_dict(), separators=(",", ":")))
            return 0
        except (OSError, ValueError, TypeError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
            return 2
        finally:
            store.close()
    if args.command == "serve":
        store = Store(args.db)
        stop_event = threading.Event()
        worker_thread = None
        try:
            coordinator = Coordinator(_build_registry(args, store), store)
            task_queue = TaskQueue(store, max_pending=int(os.environ.get("AIPOOL_MAX_PENDING", "1000")))

            def reload_registry() -> None:
                """Apply panel changes to the live coordinator without a process restart."""
                _load_local_config()
                coordinator.registry = _build_registry(args, store)

            server = make_server(
                coordinator,
                host=args.host,
                port=args.port,
                token=os.environ.get("AIPOOL_TOKEN") or None,
                queue=task_queue,
                max_pending=task_queue.max_pending,
                reload_callback=reload_registry,
            )
            if not args.no_worker:
                worker = QueueWorker(
                    task_queue,
                    coordinator,
                    worker_id=os.environ.get("AIPOOL_WORKER_ID", "worker-1"),
                    lease_seconds=float(os.environ.get("AIPOOL_LEASE_SECONDS", "60")),
                    poll_seconds=float(os.environ.get("AIPOOL_POLL_SECONDS", "0.1")),
                )
                worker_thread = threading.Thread(target=worker.run_forever, args=(stop_event,), daemon=True)
                worker_thread.start()
            try:
                server.serve_forever()
            finally:
                stop_event.set()
                if worker_thread is not None:
                    worker_thread.join(timeout=2)
                server.server_close()
        except KeyboardInterrupt:
            return 0
        finally:
            store.close()
        return 0
    if args.command == "providers":
        print(json.dumps([{
            "id": adapter.profile.id,
            "transport": adapter.profile.transport,
            "state": adapter.profile.state.value,
            "capabilities": sorted(adapter.profile.capabilities),
        } for adapter in registry.all()], separators=(",", ":")))
        return 0
    if args.command == "status":
        print(json.dumps({"providers": len(registry.all()), "database": os.environ.get("AIPOOL_DB", ":memory:")}, separators=(",", ":")))
        return 0
    if args.command == "stats":
        store = Store(args.db)
        try:
            print(json.dumps(store.stats(), separators=(",", ":")))
        finally:
            store.close()
        return 0
    if args.command == "artifact" and args.artifact_action == "upload":
        try:
            content = sys.stdin.buffer.read(128 * 1024 + 1) if args.file == "-" else Path(args.file).read_bytes()
            if len(content) > 128 * 1024:
                raise ValueError("artifact exceeds 131072 byte limit")
            if os.environ.get("AIPOOL_MODE", "local").lower() == "remote":
                reference = upload_artifact_remote(
                    os.environ.get("AIPOOL_BASE_URL", ""), content,
                    token=os.environ.get("AIPOOL_TOKEN") or None,
                    headers_extra=_cloudflare_access_headers(),
                    timeout_seconds=_remote_timeout_seconds(),
                )
            else:
                reference = ArtifactStore(os.environ.get("AIPOOL_ARTIFACT_ROOT", ".aipool-artifacts")).put(content)
            print(json.dumps({"reference": reference, "bytes": len(content)}, separators=(",", ":")))
            return 0
        except (OSError, ValueError, RemoteCoordinatorError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
            return 1
    try:
        task = _apply_origin(TaskEnvelope.from_dict(json.loads(args.task_json)))
    except (ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
        print(json.dumps({"success": False, "error": f"invalid task envelope: {exc}"}, separators=(",", ":")))
        return 2
    if os.environ.get("AIPOOL_MODE", "local").lower() == "remote":
        try:
            result = submit_remote(
                os.environ.get("AIPOOL_BASE_URL", ""), task,
                token=os.environ.get("AIPOOL_TOKEN") or None,
                headers_extra=_cloudflare_access_headers(),
                timeout_seconds=_remote_timeout_seconds(),
            )
        except RemoteCoordinatorError as exc:
            print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
            return 1
        print(json.dumps(result, separators=(",", ":")))
        return 0 if result.get("success") else 1
    store = Store(args.db)
    try:
        outcome = Coordinator(registry, store).submit(task)
        print(json.dumps({
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
        }, separators=(",", ":")))
        return 0 if outcome.success else 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
