# Distributed Compute Vertical Slice Implementation Plan

> **For agentic workers:** if this plan has more than ~4 tasks, use the `scoped-delivery` skill to implement it in 1-3 task chunks via fresh subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a testable local distributed-compute coordinator that routes compact task envelopes to legitimate providers and returns validated structured results.

**Architecture:** Python 3.13 modular monolith with typed domain objects, provider adapters, a deterministic router/quality gate, SQLite-ready repositories, and CLI-first integration. Local and OpenAI-compatible transports are isolated behind one provider protocol; the fixture adapter proves behavior without external access.

**Tech Stack:** Python 3.13, standard library (`dataclasses`, ` argparse`, `sqlite3`, `urllib`), `pytest` only if available, JSON Lines CLI protocol, optional HTTP gateway.

## Global Constraints

- Never bypass authentication, paywalls, rate limits, CAPTCHAs, provider restrictions, or hidden interfaces.
- Treat provider output as untrusted data; it must never gain coordinator instructions or tool permissions.
- Prefer deterministic preprocessing and refuse delegation when predicted orchestration cost exceeds local work.
- Keep credentials outside the repository and redact sensitive payloads from logs.
- Local models are first-class providers; unknown providers remain quarantined until validated.
- Every task ends with a focused verification command and records success, validity, latency, and approximate token/cost economics.

## File map

- `pyproject.toml`: package metadata and test command.
- `src/aipool/domain.py`: task, provider, result, capability, health, and strategy types.
- `src/aipool/providers.py`: adapter protocol plus fixture, command, and OpenAI-compatible adapters.
- `src/aipool/quality.py`: deterministic response validation.
- `src/aipool/routing.py`: task classification, delegation economics, and utility-based selection.
- `src/aipool/service.py`: orchestration and retry/fallback use case.
- `src/aipool/cli.py`: `aipool task`, `providers`, and `status` commands.
- `src/aipool/storage.py`: SQLite repositories and redacted outcome metrics.
- `tests/`: unit and integration tests, including required failure paths.
- `skills/distributed-compute/SKILL.md`: concise Claude/Codex delegation guidance.

### Task 1: Package and domain contracts

**Files:** create `pyproject.toml`, `src/aipool/__init__.py`, `src/aipool/domain.py`, `tests/test_domain.py`.

- [ ] Define frozen dataclasses `TaskEnvelope`, `ProviderProfile`, `ProviderResult`, and `TaskOutcome`; enums `TaskKind`, `Strategy`, and `ProviderState`.
- [ ] Include `input_ref`, normalized requirements, importance, max cost, and local estimate in the task envelope.
- [ ] Test JSON round trips, stable task IDs from normalized envelopes, and refusal to serialize secrets.
- [ ] Run `python -m pytest -q tests/test_domain.py`.

### Task 2: Provider adapters

**Files:** create `src/aipool/providers.py`, `tests/test_providers.py`.

- [ ] Define `ProviderAdapter.complete(task) -> ProviderResult` and a registry keyed by provider ID.
- [ ] Implement fixture output, configured subprocess execution with timeout, and OpenAI-compatible POST using `urllib`; no adapter may invent credentials or bypass controls.
- [ ] Test successful fixture calls, timeout/error normalization, request shape, and disabled-unconfigured providers.
- [ ] Run `python -m pytest -q tests/test_providers.py`.

### Task 3: Router and quality gate

**Files:** create `src/aipool/routing.py`, `src/aipool/quality.py`, `tests/test_routing_quality.py`.

- [ ] Classify inventory/classification/extraction/summarization/coding/review tasks with explicit capability requirements.
- [ ] Select the cheapest sufficiently capable healthy provider using capability, reliability, latency, and effective cost; return `NO_DELEGATION` when overhead is higher than the local estimate.
- [ ] Validate empty, boilerplate, refusal, malformed JSON, truncation, and task-inconsistent responses deterministically.
- [ ] Test cheap selection, escalation, no-delegation, garbage rejection, and unhealthy-provider exclusion.
- [ ] Run `python -m pytest -q tests/test_routing_quality.py`.

### Task 4: Orchestration service and persistence

**Files:** create `src/aipool/storage.py`, `src/aipool/service.py`, `tests/test_service.py`.

- [ ] Add SQLite schema/repositories for providers, task outcomes, capability observations, and cache keys.
- [ ] Orchestrate selection, one bounded retry/fallback, quality validation, and outcome recording; never pass provider text to a tool executor.
- [ ] Update rolling reliability/capability observations only after validated results.
- [ ] Test fallback after unusable output, unavailable-provider bypass, capability score changes, cache hits, and recorded token economics.
- [ ] Run `python -m pytest -q tests/test_service.py`.

### Task 5: CLI and shared skill

**Files:** create `src/aipool/cli.py`, `skills/distributed-compute/SKILL.md`, `tests/test_cli.py`.

- [ ] Implement JSON task submission plus `providers`, `status`, and `stats` commands with stable exit codes.
- [ ] Make CLI output compact JSON suitable for Claude/Codex context and avoid echoing secrets or full artifacts.
- [ ] Document when delegation is worthwhile, when to choose verification/cascade, and how to invoke the CLI without embedding implementation details in global instructions.
- [ ] Test end-to-end fixture submission and compact output.
- [ ] Run `python -m pytest -q`.

### Task 6: HTTP gateway and verification harness

**Files:** create `src/aipool/gateway.py`, `tests/test_gateway.py`, `docs/operations.md`.

- [ ] Expose local-only JSON endpoints for task submission, provider status, and metrics using a bounded standard-library server.
- [ ] Require an explicit local auth token when binding beyond loopback; reject oversized envelopes and malformed JSON.
- [ ] Add a verification script demonstrating submit, select, perform, reject garbage, retry/bypass, score update, no-delegation, escalation, and unavailable-provider removal.
- [ ] Run `python -m pytest -q` and the verification script.
