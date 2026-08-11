# Distributed Compute Architecture

## Decision

Start with a Python 3.13 modular monolith named `aipool`. The local CLI and HTTP
gateway share the same application services; the VPS can run the gateway as a
persistent process without requiring microservices. SQLite stores provider
metadata, health observations, usage windows, task outcomes, and cache records. Files and large
payloads are represented by content-addressed artifact references.

The first vertical slice is intentionally narrow:

```text
Claude/Codex skill -> aipool CLI/HTTP -> task classifier/router
                   -> local provider or OpenAI-compatible provider
                   -> deterministic quality gate -> registry/outcome record
```

## Boundaries

- `domain`: typed task envelopes, provider metadata, outcomes, and state enums.
- `providers`: adapter protocol plus local subprocess and OpenAI-compatible HTTP
  adapters. Provider-specific authentication and request formatting stay here.
- `routing`: cheap task classification, delegation-worthwhile check, utility
  scoring, cascade/verification strategy selection, and provider selection.
- `quality`: deterministic checks for empty, refusal, boilerplate, malformed JSON,
  truncation, and task-inconsistent output. Provider text is always data.
- `storage`: SQLite repositories, artifact hashing, and redacted event metrics.
- `service`: orchestration use cases exposed by CLI and HTTP transports.
- `cli` / `gateway`: stable caller interfaces; neither contains provider logic.

## Safety and economics

Provider output never becomes coordinator instructions or tool calls. Credentials
come only from environment variables or a local ignored config file. Prompts use
artifact references and compact envelopes where possible. The router records
orchestration overhead, worker usage, latency, validity, and whether delegation
was predicted to save primary-model work; it declines delegation when estimated
overhead exceeds the local-work estimate.

Configured request/token windows are persisted per provider. Exhausted providers
enter a time-bounded `RATE_LIMITED` hold, and HTTP 429 `Retry-After` values extend
that hold. Unknown or discovered providers enter `QUARANTINED` and cannot route
production tasks until probes and adapter tests pass. Health failures use exponential backoff;
rate-limited, auth-required, broken, and disabled providers are excluded from the
active pool.

## Initial providers

1. `local-command`: an explicit user-configured command, useful for Ollama or a
   local model wrapper; disabled unless configured.
2. `openai-compatible`: an adapter for a user-authorized endpoint and API key;
   disabled unless configured.
3. `fixture`: deterministic test provider, never a production provider.
4. `browser-chat`: an operator-injected browser-session transport for a public
   chat UI whose reviewed terms do not explicitly prohibit the intended
   external use; it does not log in, bypass challenges, discover hidden
   endpoints, or assume that no-key access resolves every legal or policy issue.
   A model-guided variant can inspect the visible accessibility snapshot and
   select model/options controls through a bounded action plan; it cannot log in,
   navigate to hidden endpoints, or rotate profiles to evade usage limits.

The adapter protocol leaves room for permitted CLI and future remote-node
providers without adding transport assumptions to routing. All transports use
the same bounded context packet so a provider with no API can still receive
reconstructable task context through its permitted UI.

## Integration

Install or symlink the canonical repository skill at
`skills/distributed-compute` into the native skill directory for the user's
agent (for example `~/.claude/skills/distributed-compute` or
`~/.codex/skills/distributed-compute`). The
concise skill teaches when not to
delegate, how to call `aipool task`, and when to request `verify`, `consensus`, or
`cascade`. Heavy design and operations material remains in this repository.

The local CLI is the first stable interface. A small JSON HTTP API is added for the
VPS coordinator after the in-process path is tested; it accepts task envelopes and
returns structured outcomes, not arbitrary tool permissions.

When no provider is sufficiently capable or delegation is not economical, the
coordinator returns a successful routing outcome with `native_fallback: true` and
`next_action: "native_model"`. Claude or Codex completes that task locally rather
than retrying the same request, then may submit subsequent independent tasks.

## Delivery phases

1. Vertical slice: envelopes, registry, fixture/local adapters, router, quality gate,
   CLI, tests, and concise skill.
2. Persistence and operations: SQLite, health monitor, retries/backoff, metrics,
   cache, artifacts, and HTTP gateway.
3. Capability learning: benchmark fixtures, production score updates, exploration,
   verification strategies, and savings reporting.
4. Discovery and scale: candidate-provider quarantine, VPS queue, additional nodes,
   and authorized adapter repair workflow.
