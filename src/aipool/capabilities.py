"""Shared capability-discovery contract for callers and the gateway."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .domain import ProviderProfile, ProviderState, TaskKind
from .routing import supports_task


_CAPABILITY_CLASSES: tuple[dict[str, Any], ...] = (
    {
        "name": "quick_compute",
        "task_kinds": (TaskKind.INVENTORY.value, TaskKind.CLASSIFICATION.value, TaskKind.EXTRACTION.value, TaskKind.SUMMARIZATION.value),
        "use_for": "bounded scans, labels, structured extraction, and short summaries",
    },
    {"name": "coding", "task_kinds": (TaskKind.CODING.value,), "use_for": "bounded implementation suggestions, patches, and code transformations"},
    {"name": "reasoning", "task_kinds": (TaskKind.REVIEW.value, TaskKind.RESEARCH.value), "use_for": "bounded code review, comparison, and evidence-backed research"},
)


def capability_document(profiles: Iterable[ProviderProfile] = ()) -> dict[str, object]:
    """Build the caller-facing document from the same live profiles the router uses."""
    profile_list = tuple(profiles)
    routes: list[dict[str, object]] = []
    for profile in profile_list:
        if profile.state not in {ProviderState.HEALTHY, ProviderState.DEGRADED}:
            continue
        routes.append({
            "provider_id": profile.id,
            "name": profile.name,
            "transport": profile.transport,
            "state": profile.state.value,
            "capabilities": sorted(profile.capabilities),
        })
    capabilities = []
    for item in _CAPABILITY_CLASSES:
        task_kinds = tuple(item["task_kinds"])
        relevant_ids = {
            profile.id for profile in profile_list
            if profile.state in {ProviderState.HEALTHY, ProviderState.DEGRADED}
            and any(supports_task(profile, kind) for kind in task_kinds)
        }
        relevant = [route for route in routes if route["provider_id"] in relevant_ids]
        capabilities.append({**item, "task_kinds": list(task_kinds), "routes": relevant})
    return {
        "coordinator": "auto",
        "capabilities": capabilities,
        "commands": {
            "artifact_upload": {
                "command": "~/.agents/bin/aipool artifact upload --file PATH",
                "returns": "JSON containing reference and bytes",
            },
            "task": {
                "command": "~/.agents/bin/aipool task --json ENVELOPE_JSON",
                "required": ["task", "input_ref"],
                "optional": ["requirements", "strategy", "importance", "max_cost", "local_estimate"],
                "returns": "JSON containing success, valid, output, provider_id, native_fallback, and reason",
            },
            "queue_submit": {
                "command": "~/.agents/bin/aipool queue submit --json ENVELOPE_JSON",
                "returns": "JSON containing task_id and queued status; inspect with queue status",
            },
        },
        "routing": "The coordinator selects the provider and task-specialized route; callers do not select a model.",
    }
