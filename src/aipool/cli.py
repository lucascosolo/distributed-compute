"""Compact command-line interface for agent callers."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from .domain import ProviderProfile, ProviderState, TaskEnvelope
from .providers import CommandAdapter, FixtureAdapter, OpenAICompatibleAdapter, ProviderRegistry
from .service import Coordinator
from .storage import Store


def _load_local_config() -> None:
    """Load ignored operator config without overwriting explicit environment values."""
    candidates = []
    configured = os.environ.get("AIPOOL_CONFIG_FILE")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend((Path.cwd() / ".aipool.local", Path.home() / ".agents" / "distributed-compute.env"))
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
            reliability=0.5, state=ProviderState.HEALTHY,
        )
        registry.register(FixtureAdapter(profile, lambda _: fixture_output))
    command = os.environ.get("AIPOOL_COMMAND")
    if command:
        profile = ProviderProfile(
            "local-command", "Configured local command", "command",
            capabilities={"classification": 0.7, "structured_json": 0.7, "extraction": 0.7, "summarization": 0.5},
            reliability=0.5, state=ProviderState.HEALTHY,
        )
        registry.register(CommandAdapter(profile, tuple(shlex.split(command))))
    endpoint = os.environ.get("AIPOOL_OPENAI_ENDPOINT")
    if endpoint and os.environ.get("AIPOOL_OPENAI_MODEL"):
        profile = ProviderProfile(
            "openai-compatible", "Configured OpenAI-compatible provider", "openai-compatible",
            capabilities={"classification": 0.8, "structured_json": 0.8, "extraction": 0.8, "summarization": 0.8, "coding": 0.7, "instruction_following": 0.8},
            reliability=0.5, state=ProviderState.HEALTHY,
        )
        registry.register(OpenAICompatibleAdapter(profile, endpoint, os.environ["AIPOOL_OPENAI_MODEL"], "AIPOOL_OPENAI_API_KEY"))
    return registry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aipool")
    subparsers = parser.add_subparsers(dest="command", required=True)
    task = subparsers.add_parser("task", help="submit one compact task envelope")
    task.add_argument("--json", required=True, dest="task_json")
    task.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    subparsers.add_parser("providers", help="list configured providers")
    subparsers.add_parser("status", help="show coordinator status")
    stats = subparsers.add_parser("stats", help="show delegation economics and provider usage")
    stats.add_argument("--db", default=os.environ.get("AIPOOL_DB", ":memory:"))
    return parser


def main(argv: list[str] | None = None) -> int:
    _load_local_config()
    args = _parser().parse_args(argv)
    registry = _build_registry(args)
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
