"""Compact command-line interface for agent callers."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import threading
from dataclasses import asdict
from pathlib import Path

from .client import cancel_remote, enqueue_remote, get_remote_queue, RemoteCoordinatorError, submit_remote
from .artifacts import ArtifactStore
from .benchmark import run_benchmark
from .discovery import CandidateRegistry, CommandCandidateProbe, QuarantineProbePipeline, promote_lead
from .discovery_sources import DiscoveryRunner, HtmlPageSource, LeadRegistry, LocalCatalogSource, RedditSearchSource, RedditThreadSource
from .discord_api import DiscordApiClient, DiscordChannelAdapter
from .domain import ProviderErrorKind, ProviderProfile, ProviderState, TaskEnvelope
from .gateway import make_server
from .queue import QueueFull, TaskQueue, record_to_dict
from .providers import BrowserCommandAdapter, CandidateCommandAdapter, CommandAdapter, FixtureAdapter, HuggingFaceInferenceAdapter, OpenAICompatibleAdapter, ProviderRegistry
from .provider_catalog import config_prefix, load_catalog, model_config_prefix
from .service import Coordinator
from .storage import Store
from .worker import QueueWorker


def _load_local_config() -> None:
    """Load ignored operator config without overwriting explicit environment values."""
    candidates = []
    configured = os.environ.get("AIPOOL_CONFIG_FILE")
    if configured:
        candidates.append(Path(configured).expanduser())
    else:
        candidates.extend((
            Path.cwd() / ".aipool.local",
            Path.home() / ".claude" / "distributed-compute.env",
            Path.home() / ".codex" / "distributed-compute.env",
            Path.home() / ".agents" / "distributed-compute.env",
        ))
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
                os.environ.setdefault(key, value)
        break


def _build_registry(args: argparse.Namespace) -> ProviderRegistry:
    registry = ProviderRegistry()
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
    endpoint = os.environ.get("AIPOOL_OPENAI_ENDPOINT")
    if endpoint and os.environ.get("AIPOOL_OPENAI_MODEL"):
        profile = ProviderProfile(
            "openai-compatible", "Configured OpenAI-compatible provider", "openai-compatible",
            capabilities={"classification": 0.8, "structured_json": 0.8, "extraction": 0.8, "summarization": 0.8, "coding": 0.7, "instruction_following": 0.8},
            reliability=0.5, state=ProviderState.HEALTHY, max_complexity=4,
        )
        registry.register(OpenAICompatibleAdapter(profile, endpoint, os.environ["AIPOOL_OPENAI_MODEL"], "AIPOOL_OPENAI_API_KEY"))
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
            ),
        ))
    for catalog_provider in load_catalog():
        provider_prefix = config_prefix(catalog_provider)
        model_prefix = model_config_prefix(catalog_provider)
        if os.environ.get(f"{model_prefix}_ENABLED", "").casefold() not in {"1", "true", "yes", "on"}:
            continue
        api_key_env = f"{provider_prefix}_API_KEY"
        if not os.environ.get(api_key_env):
            continue
        model = os.environ.get(f"{model_prefix}_MODEL") or catalog_provider.model
        power = catalog_provider.power.casefold()
        max_complexity = 1 if power == "light" else 2 if power == "medium" else 3 if power == "strong" else 4
        capabilities = {"classification": 0.6, "structured_json": 0.6, "extraction": 0.6, "summarization": 0.6}
        if max_complexity >= 3:
            capabilities.update({"coding": 0.7, "instruction_following": 0.7})
        profile = ProviderProfile(
            f"catalog:{catalog_provider.slug}", catalog_provider.name, catalog_provider.transport,
            capabilities=capabilities, reliability=0.2, state=ProviderState.QUARANTINED,
            max_complexity=max_complexity, quota_weight=catalog_provider.quota_weight,
        )
        if catalog_provider.transport == "openai-compatible":
            endpoint = os.environ.get(f"{provider_prefix}_ENDPOINT") or catalog_provider.endpoint
            if not endpoint.rstrip("/").endswith("/chat/completions"):
                endpoint = endpoint.rstrip("/") + "/chat/completions"
            registry.register(OpenAICompatibleAdapter(profile, endpoint, model, api_key_env))
        elif catalog_provider.transport == "huggingface-api":
            registry.register(HuggingFaceInferenceAdapter(profile, model, api_key_env, catalog_provider.endpoint))
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
            ArtifactStore(os.environ.get("AIPOOL_ARTIFACT_ROOT", ".aipool-artifacts")),
        ))
    discord_token = os.environ.get("AIPOOL_DISCORD_BOT_TOKEN")
    discord_guild = os.environ.get("AIPOOL_DISCORD_GUILD_ID")
    discord_channel = os.environ.get("AIPOOL_DISCORD_CHANNEL_ID")
    if args.command != "discord" and discord_token and discord_guild and discord_channel:
        try:
            client = DiscordApiClient(discord_token, discord_guild, discord_channel)
            discovered = client.list_bots()
        except (ValueError, TypeError):
            discovered = []
        if not isinstance(discovered, list):
            discovered = []
        controller_id = os.environ.get("AIPOOL_DISCORD_APPLICATION_ID", "")
        artifacts = ArtifactStore(os.environ.get("AIPOOL_ARTIFACT_ROOT", ".aipool-artifacts"))
        for bot in discovered:
            bot_id = bot["id"]
            if bot_id == controller_id:
                continue
            profile = ProviderProfile(
                f"discord-worker:{bot_id}", f"Discord worker {bot.get('username', bot_id)}", "discord",
                capabilities={"classification": 0.5, "extraction": 0.4, "summarization": 0.4},
                reliability=0.2, state=ProviderState.QUARANTINED, max_complexity=1,
            )
            registry.register(DiscordChannelAdapter(
                profile, discord_token, discord_channel, bot_id,
                controller_bot_id=controller_id,
                message_prefix=os.environ.get("AIPOOL_DISCORD_MESSAGE_PREFIX", ""),
                artifacts=artifacts,
            ))
    return registry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aipool")
    subparsers = parser.add_subparsers(dest="command", required=True)
    task = subparsers.add_parser("task", help="submit one compact task envelope")
    task.add_argument("--json", required=True, dest="task_json")
    task.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    subparsers.add_parser("providers", help="list configured providers")
    discord = subparsers.add_parser("discord", help="verify the configured Discord controller")
    discord_subparsers = discord.add_subparsers(dest="discord_action", required=True)
    discord_subparsers.add_parser("check", help="read-only bot/server/channel connectivity check")
    discord_bots = discord_subparsers.add_parser("bots", help="list bot members visible in the configured server")
    discord_bots.add_argument("--limit", type=int, default=1000)
    discord_recent = discord_subparsers.add_parser("recent", help="read recent diagnostic messages from the configured test channel")
    discord_recent.add_argument("--limit", type=int, default=50)
    discord_hold = discord_subparsers.add_parser("hold", help="disable one discovered worker without sending a message")
    discord_hold.add_argument("--username", required=True, help="exact discovered Discord bot username")
    discord_hold.add_argument("--reason", required=True, help="operator evidence for holding this worker")
    discord_hold.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    discord_benchmark = discord_subparsers.add_parser("benchmark", help="run bounded capability cases against discovered worker bots")
    discord_benchmark.add_argument("--max-bots", type=int, default=3)
    discord_benchmark.add_argument("--include-degraded", action="store_true", help="retest workers already showing failures")
    discord_benchmark.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
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
    stats = subparsers.add_parser("stats", help="show delegation economics and provider usage")
    stats.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
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
    if args.command == "discord" and args.discord_action == "check":
        try:
            result = DiscordApiClient(
                os.environ.get("AIPOOL_DISCORD_BOT_TOKEN", ""),
                os.environ.get("AIPOOL_DISCORD_GUILD_ID", ""),
                os.environ.get("AIPOOL_DISCORD_CHANNEL_ID", ""),
            ).check()
            print(json.dumps(result, separators=(",", ":")))
            return 0
        except (ValueError, TypeError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
            return 2
    if args.command == "discord" and args.discord_action == "bots":
        try:
            client = DiscordApiClient(
                os.environ.get("AIPOOL_DISCORD_BOT_TOKEN", ""),
                os.environ.get("AIPOOL_DISCORD_GUILD_ID", ""),
                os.environ.get("AIPOOL_DISCORD_CHANNEL_ID", ""),
            )
            print(json.dumps({"bots": client.list_bots(args.limit)}, separators=(",", ":")))
            return 0
        except (ValueError, TypeError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
            return 2
    if args.command == "discord" and args.discord_action == "recent":
        try:
            client = DiscordApiClient(
                os.environ.get("AIPOOL_DISCORD_BOT_TOKEN", ""),
                os.environ.get("AIPOOL_DISCORD_GUILD_ID", ""),
                os.environ.get("AIPOOL_DISCORD_CHANNEL_ID", ""),
            )
            print(json.dumps({"messages": client.recent_messages(args.limit)}, separators=(",", ":")))
            return 0
        except (ValueError, TypeError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
            return 2
    if args.command == "discord" and args.discord_action == "hold":
        store = Store(args.db)
        try:
            username = args.username.strip()
            reason = args.reason.strip()
            if not username or not reason:
                raise ValueError("username and reason are required")
            client = DiscordApiClient(
                os.environ.get("AIPOOL_DISCORD_BOT_TOKEN", ""),
                os.environ.get("AIPOOL_DISCORD_GUILD_ID", ""),
                os.environ.get("AIPOOL_DISCORD_CHANNEL_ID", ""),
            )
            matches = [bot for bot in client.list_bots() if bot.get("username") == username]
            if len(matches) != 1:
                raise ValueError(f"expected exactly one discovered bot named {username!r}, found {len(matches)}")
            bot = matches[0]
            provider_id = f"discord-worker:{bot['id']}"
            profile = ProviderProfile(
                provider_id, f"Discord worker {username}", "discord",
                capabilities={"classification": 0.5, "extraction": 0.4, "summarization": 0.4},
                reliability=0.2, state=ProviderState.QUARANTINED, max_complexity=1,
            )
            store.ensure_health(profile)
            store.set_health(
                provider_id, state=ProviderState.DISABLED, next_probe_at=0,
                last_failure_reason=reason[:500],
            )
            print(json.dumps({"provider_id": provider_id, "username": username, "state": "disabled"}, separators=(",", ":")))
            return 0
        except (KeyError, ValueError, TypeError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
            return 2
        finally:
            store.close()
    if args.command == "discord" and args.discord_action == "benchmark":
        store = Store(args.db)
        try:
            if not 1 <= args.max_bots <= 32:
                raise ValueError("max-bots must be between 1 and 32")
            client = DiscordApiClient(
                os.environ.get("AIPOOL_DISCORD_BOT_TOKEN", ""),
                os.environ.get("AIPOOL_DISCORD_GUILD_ID", ""),
                os.environ.get("AIPOOL_DISCORD_CHANNEL_ID", ""),
            )
            controller_id = os.environ.get("AIPOOL_DISCORD_APPLICATION_ID", "")
            workers = [bot for bot in client.list_bots() if bot["id"] != controller_id][:args.max_bots]
            registry = ProviderRegistry()
            for bot in workers:
                bot_id = bot["id"]
                profile = ProviderProfile(
                    f"discord-worker:{bot_id}", f"Discord worker {bot.get('username', bot_id)}", "discord",
                    capabilities={"classification": 0.5, "extraction": 0.4, "summarization": 0.4},
                    reliability=0.2, state=ProviderState.QUARANTINED, max_complexity=1,
                )
                registry.register(DiscordChannelAdapter(
                    profile, os.environ["AIPOOL_DISCORD_BOT_TOKEN"],
                    os.environ["AIPOOL_DISCORD_CHANNEL_ID"], bot_id,
                    controller_bot_id=controller_id,
                    message_prefix=os.environ.get("AIPOOL_DISCORD_MESSAGE_PREFIX", ""),
                    artifacts=ArtifactStore(os.environ.get("AIPOOL_ARTIFACT_ROOT", ".aipool-artifacts")),
                ))
            coordinator = Coordinator(registry, store)
            results = []
            skipped = []
            shared_rate_limited = False
            effective_profiles = {
                profile.id: profile for profile in coordinator.health.profiles(
                    adapter.profile for adapter in registry.all()
                )
            }
            for adapter in registry.all():
                if shared_rate_limited:
                    skipped.append({"provider_id": adapter.profile.id, "state": "shared_rate_limited"})
                    continue
                state = effective_profiles[adapter.profile.id].state
                held_states = {
                    ProviderState.RATE_LIMITED, ProviderState.AUTH_REQUIRED,
                    ProviderState.BROKEN, ProviderState.DISABLED,
                }
                if not args.include_degraded:
                    held_states.add(ProviderState.DEGRADED)
                if state in held_states:
                    skipped.append({"provider_id": adapter.profile.id, "state": state.value})
                    continue
                result = coordinator.benchmark_provider(adapter.profile.id)
                results.append({
                    "provider_id": result.provider_id, "scores": result.scores,
                    "attempts": result.attempts, "valid": result.valid,
                    "stopped_error": result.stopped_error.value if result.stopped_error else None,
                    "retry_after_seconds": result.retry_after_seconds,
                })
                if result.stopped_error == ProviderErrorKind.RATE_LIMITED:
                    shared_rate_limited = True
            print(json.dumps({"workers": results, "skipped": skipped}, separators=(",", ":")))
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
                task = TaskEnvelope.from_dict(json.loads(args.task_json))
            except (ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
                print(json.dumps({"success": False, "error": f"invalid task envelope: {exc}"}, separators=(",", ":")))
                return 2
            try:
                if mode == "remote":
                    result = enqueue_remote(base_url, task, token=token, idempotency_key=args.idempotency_key)
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
                result = operation(base_url, args.task_id, token=token)
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
    if args.command == "serve":
        store = Store(args.db)
        stop_event = threading.Event()
        worker_thread = None
        try:
            coordinator = Coordinator(registry, store)
            task_queue = TaskQueue(store, max_pending=int(os.environ.get("AIPOOL_MAX_PENDING", "1000")))
            server = make_server(
                coordinator,
                host=args.host,
                port=args.port,
                token=os.environ.get("AIPOOL_TOKEN") or None,
                queue=task_queue,
                max_pending=task_queue.max_pending,
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
    try:
        task = TaskEnvelope.from_dict(json.loads(args.task_json))
    except (ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
        print(json.dumps({"success": False, "error": f"invalid task envelope: {exc}"}, separators=(",", ":")))
        return 2
    if os.environ.get("AIPOOL_MODE", "local").lower() == "remote":
        try:
            result = submit_remote(
                os.environ.get("AIPOOL_BASE_URL", ""), task,
                token=os.environ.get("AIPOOL_TOKEN") or None,
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
