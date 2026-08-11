"""Compact command-line interface for agent callers."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import threading
from pathlib import Path

from .client import cancel_remote, enqueue_remote, get_remote_queue, RemoteCoordinatorError, submit_remote
from .artifacts import ArtifactStore
from .discovery import CandidateRegistry, promote_lead
from .discovery_sources import DiscoveryRunner, LeadRegistry, RedditSearchSource, RedditThreadSource
from .domain import ProviderProfile, ProviderState, TaskEnvelope
from .gateway import make_server
from .queue import QueueFull, TaskQueue, record_to_dict
from .providers import BrowserCommandAdapter, CommandAdapter, FixtureAdapter, OpenAICompatibleAdapter, ProviderRegistry
from .service import Coordinator
from .storage import Store
from .worker import QueueWorker


def _load_local_config() -> None:
    """Load ignored operator config without overwriting explicit environment values."""
    candidates = []
    configured = os.environ.get("AIPOOL_CONFIG_FILE")
    if configured:
        candidates.append(Path(configured).expanduser())
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
            if key.startswith("AIPOOL_"):
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
    return registry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aipool")
    subparsers = parser.add_subparsers(dest="command", required=True)
    task = subparsers.add_parser("task", help="submit one compact task envelope")
    task.add_argument("--json", required=True, dest="task_json")
    task.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    subparsers.add_parser("providers", help="list configured providers")
    discover = subparsers.add_parser("discover", help="collect bounded public chatbot discovery leads")
    discover_input = discover.add_mutually_exclusive_group(required=True)
    discover_input.add_argument("--query")
    discover_input.add_argument("--thread-url")
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
