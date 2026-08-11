# Implementation progress

This file is the compact handoff ledger for future sessions. It records project
state, not deployment secrets. Operator-specific hosts, tokens, credentials,
database paths, and artifact roots belong only in the gitignored `.aipool.local`
or the operator's environment.

## Current checkpoint

- Branch: `main`
- Last pushed commit: `aa96798`
- Working tree at the last checkpoint: clean
- Verification for this chunk: `82 tests passed` with
  `-W error::ResourceWarning`
- No VPS deployment has been performed.

## Implemented

- Frozen task/provider/result contracts with deterministic IDs and secret-like
  requirement-key rejection.
- Fixture, bounded command, and OpenAI-compatible provider adapters.
- Capability- and complexity-aware routing with a native-model fallback when
  delegation is unsupported or not cheaper than the local estimate.
- Deterministic quality validation, health/backoff state, SQLite outcomes,
  observations, cache, artifacts, and economics reporting.
- Capability benchmarks persisted as empirical evidence, including discovery of
  previously undeclared capabilities and bounded registered-provider probing.
- `single`, `verify`, three-provider `consensus`, explicit-scope `map`, and
  bounded `map_reduce` strategies.
- Local CLI, authenticated standard-library gateway, remote HTTP client, status
  and metrics endpoints, ignored operator configuration, and a generic systemd
  example containing placeholders only.
- Durable queue core in `src/aipool/queue.py`: idempotency keys, queue bounds,
  expiring worker leases, reclaim, wrong-lease protection, and cooperative
  cancellation. `QueueWorker` now claims, executes, completes, and skips queued
  cancellations; the authenticated gateway exposes enqueue, inspect, and cancel
  endpoints, and `serve` starts the worker unless `--no-worker` is specified.
- Provider usage windows now support configured request/token limits with
  persistent SQLite accounting. Exhausted providers enter `RATE_LIMITED` hold
  until the window ends; HTTP 429 `Retry-After` values extend health backoff.
- Operator queue commands now support local and authenticated remote
  `submit`, `status`, and `cancel` operations. The supervised worker records a
  bounded failure outcome when a coordinator invocation raises, and exits
  cleanly when its stop event is set.
- Public setup instructions use HTTPS cloning and native per-agent skill
  directories (`~/.claude/skills` or `~/.codex/skills`); CLI config discovery
  supports matching per-agent operator environment files while retaining the
  legacy shared operator path for compatibility.
- Provider registration now validates identity, adapter shape, finite limits,
  and capability scores. `aipool.discovery` keeps sourced candidates separate
  from active adapters, rejects prohibited access/evasion language, and
  requires explicit operator approval before activation.
- Candidate records now persist in SQLite, including quarantine state and
  probe evidence. The bounded quarantine probe pipeline executes only an
  injected, operator-authorized probe, never fetches arbitrary endpoints, and
  promotes a candidate to `PROBED` only when availability, authorization,
  context, output, restrictions, cost, and automation checks all pass.
- Public README, provider authorization policy, and repository-copyable
  Claude/Codex skill. The installed skill is synchronized at
  `~/.agents/skills/distributed-compute/SKILL.md`.

## Non-negotiable design decisions

1. Capability gates are more important than nominal price. A weak provider must
   not receive complex coding, architecture, security, or planning work.
2. Delegation is refused unless its predicted total cost is strictly lower than
   the primary model's local-work estimate. Composite strategies account for
   each provider call.
3. A refusal, disagreement, insufficient capability, failed quality gate, or
   uneconomical subtask returns a native fallback signal. The native Claude or
   Codex session completes that work and can continue delegating later tasks.
4. Provider output is untrusted data and never becomes coordinator instructions
   or tool permissions.
5. Only authorized provider access is allowed. Never bypass authentication,
   paywalls, CAPTCHAs, quotas, rate limits, safeguards, or provider Terms of
   Service.

## Next scoped chunk

Add provider-neutral context packets and artifact references so an authorized
public web chatbot or other constrained provider receives enough bounded,
reconstructable task context without receiving secrets or untrusted control
instructions. Public UI access must remain subject to each provider's terms,
automation rules, and rate limits; no-key access is not treated as automatic
authorization.

Only after those checks consider a VPS deployment using the deploy skill. Do not
put a real VPS address or token in the repository.

## Verification command

```bash
PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests -q
```
