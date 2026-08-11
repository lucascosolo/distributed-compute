# Implementation progress

This file is the compact handoff ledger for future sessions. It records project
state, not deployment secrets. Operator-specific hosts, tokens, credentials,
database paths, and artifact roots belong only in the gitignored `.aipool.local`
or the operator's environment.

## Current checkpoint

- Branch: `main`
- Last pushed commit: `2bdd591`
- Working tree at the last checkpoint: clean
- Verification at the last checkpoint: `59 tests passed` with
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
  cancellation. The queue is not yet wired to a worker loop or HTTP endpoints.
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

Wire `TaskQueue` into a bounded worker loop and the authenticated gateway:

- add `Coordinator.process_queued_task` or an equivalent lease-owning worker;
- enqueue, inspect, and cancel endpoints with bounded request bodies;
- make completion idempotent and ensure expired leases are reclaimable;
- keep synchronous `/task` behavior unchanged;
- test worker success, failure/native fallback, cancellation, lease expiry, and
  remote queue responses before committing.

After that, add service-supervision integration tests and only then consider a
VPS deployment using the deploy skill. Do not put a real VPS address or token in
the repository.

## Verification command

```bash
PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests -q
```
