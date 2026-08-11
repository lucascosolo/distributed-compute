# Distributed Compute Roadmap

This roadmap moves from a reliable local MVP to a system that can discover and
validate new legitimate compute providers. Each chunk has a narrow scope, a
working boundary, and a verification gate. Do not start the next chunk until its
gate passes.

## Guiding order

Build the smallest useful execution path first, then add persistence, safety,
measurement, and scale. Provider discovery comes last because discovering more
providers is useless until the coordinator can prove that a provider is safe,
useful, authorized, and cheaper than existing options.

## Phase 0 — Foundations and contracts

### Chunk 0.1: Repository and domain contracts

Create the Python package, typed task envelopes, provider profiles, results,
health states, capabilities, strategies, and configuration boundaries.

**Exit gate:** domain objects serialize deterministically; credentials and raw
secrets cannot appear in serialized task data; focused unit tests pass.

### Chunk 0.2: Provider adapter contract

Define one adapter interface and normalized error/result types. Add a deterministic
fixture adapter for tests, while keeping local-command and OpenAI-compatible
adapters behind the same boundary.

**Exit gate:** every adapter returns the same result shape and bounded failures do
not escape as provider-specific exceptions.

## Phase 1 — MVP vertical slice

### Chunk 1.1: One useful local provider

Implement the configured local-command adapter. Support explicit commands such as
an Ollama wrapper, timeouts, cancellation, output-size limits, and disabled state
when no command is configured.

**Exit gate:** a real user-configured local model can complete a small JSON task;
the fixture adapter covers the same path without external services.

### Chunk 1.2: Compact task router

Classify a small task vocabulary: inventory, classification, extraction,
summarization, coding, review, and research. Select the least expensive healthy
provider whose capabilities meet the task requirements. Refuse delegation when
the estimated envelope and coordination cost exceed the local-work estimate.

**Exit gate:** cheap routine tasks route cheaply, difficult tasks escalate, and
uneconomical or unsupported tasks return an explicit native-model fallback so
Claude/Codex can complete them locally and continue delegating later subtasks.

### Chunk 1.3: Deterministic quality gate

Reject empty output, boilerplate, refusal-only output, malformed required JSON,
obvious truncation, repeated garbage, and task-inconsistent responses. Keep
provider output as data; never interpret it as coordinator instructions.

**Exit gate:** garbage output is rejected and produces a structured failure rather
than reaching the caller as a successful result.

### Chunk 1.4: Stable caller interface

Ship the compact `aipool task`, `aipool providers`, and `aipool status` CLI
commands plus the concise Claude/Codex shared skill. Return machine-readable JSON
and avoid copying large artifacts into prompts.

**MVP gate:** Claude or Codex can submit one task, the coordinator chooses a
worker, the worker completes it, invalid output is rejected, and the caller gets
a compact structured result without knowing the provider.

## Phase 2 — Reliability and safety

### Chunk 2.1: Persistent registry and outcome journal

Add SQLite persistence for provider definitions, capability observations, health
events, task outcomes, cache keys, latency, validity, worker usage, orchestration
cost, and estimated primary-model savings.

**Exit gate:** restart does not erase provider reputation or metrics; sensitive
prompt contents and credentials are not written to logs or the database.

### Chunk 2.2: Health state machine

Implement probes, rolling success/latency/validity windows, exponential backoff,
and transitions through `HEALTHY`, `DEGRADED`, `RATE_LIMITED`, `AUTH_REQUIRED`,
`BROKEN`, `QUARANTINED`, and `DISABLED`.

**Exit gate:** failing providers leave the routing pool, are not repeatedly
hammered, and recover only after successful validation.

### Chunk 2.3: Retry and fallback orchestration

Add bounded retries for transient failures, provider bypass for unavailable
workers, and escalation after validation or confidence failure. Never retry
authentication failures or rate-limit failures without respecting backoff.

**Exit gate:** a failed provider falls back safely, retry counts are bounded, and
all decisions are recorded.

### Chunk 2.4: Artifact references and caching

Add content-addressed artifacts for files, command output, repository snapshots,
and worker results. Cache only safe deterministic or semideterministic work using
task type, normalized requirements, input hashes, provider/model, and relevant
configuration.

**Exit gate:** unchanged inputs reuse valid results; changed inputs cannot receive
stale results; large payloads are not repeatedly copied through model contexts.

## Phase 3 — Measurement and useful coordination

### Chunk 3.1: Capability benchmarks

Create a compact benchmark suite for extraction, classification, summarization,
code explanation, small coding, reasoning, instruction following, and JSON output.
Run it sparingly and store per-capability scores rather than one global rank.

**Exit gate:** a provider can be strong for classification and weak for coding
without the registry collapsing those facts into one score.

### Chunk 3.2: Production reputation learning

Update capability and reliability scores from validated production outcomes. Weight
recent evidence, record sample size and confidence, and separate benchmark scores
from observed task performance.

**Exit gate:** routing changes after observed performance changes and unknown
providers receive exploration traffic only for low-risk tasks.

### Chunk 3.3: Execution strategies

Implement `single`, `verify`, `consensus`, `map`, `map_reduce`, and `cascade` with
explicit budgets, task importance, parallelism limits, and synthesis boundaries.

**Exit gate:** important results can be independently checked while low-risk tasks
remain cheaper than direct primary-model execution.

### Chunk 3.4: Savings and operations reporting

Expose provider usage, capability strengths, failure trends, avoided paid-model
work, delegation overhead, latency, cache benefit, and task classes where
delegation loses money or context.

**Exit gate:** `aipool stats` can answer which workers are useful and whether the
system is actually saving tokens and latency.

## Phase 4 — Persistent coordinator and controlled scale

### Chunk 4.1: Local HTTP gateway

Expose the stable task, status, and metrics API over a bounded standard-library
server. Keep loopback binding as the default and require explicit local
authentication before non-loopback binding.

**Exit gate:** Claude/Codex can use the same interface locally or through an
authorized tunnel without provider details entering the caller contract.

### Chunk 4.2: VPS control plane

Move persistence, registry, routing, health monitoring, artifacts, and metrics to
the VPS as a modular monolith. Keep a thin local CLI/client and preserve local
providers as first-class nodes.

**Exit gate:** the coordinator survives local client restarts, handles bounded
parallel work, and exposes no unauthenticated public endpoint.

### Chunk 4.3: Queue and additional compute nodes

Add a durable bounded queue, leases, cancellation, idempotency keys, node
registration, and resource limits. Support additional user-controlled machines
without making provider adapters aware of node placement.

**Exit gate:** duplicate delivery is safe, abandoned work is reclaimed, and one
busy or offline node does not block unrelated tasks.

## Phase 5 — Candidate provider discovery

### Chunk 5.1: Discovery source registry

Define documented discovery sources: official provider catalogs, public model
registries, user-supplied endpoints, local model inventories, services that
explicitly provide developer access, public chatbot directories, bounded search
results, and community discussions such as Reddit. Public task-oriented web
assistants (shopping, support, travel, coding, and similar) are valid leads even
when they have no API or account-based developer access. Store candidates
separately from active providers.

Discovery retrieval must be bounded, rate-limited, provenance-preserving, and
source-policy aware. A discussion post or public page is a lead, not evidence
of authorization or capability.

**Exit gate:** discovery only creates candidate records with provenance, terms,
authorization requirements, transport metadata, and a quarantine state.

The checked-in `providers/candidate-catalog.json` is an operator-supplied seed
catalog only. It is intentionally not a default active provider list; that list
will be generated after the bounded external test run.

### Chunk 5.2: Candidate normalization and policy filter

Normalize candidate metadata and reject candidates that require credential abuse,
CAPTCHA bypass, hidden endpoints, rate-limit evasion, stolen sessions, prohibited
automation, or unclear authorization. Require explicit operator approval for
ambiguous sources.

**Exit gate:** policy tests demonstrate that unsafe or unauthorized candidates can
never enter the activation pipeline.

### Chunk 5.3: Quarantine probe pipeline

Probe candidates with bounded requests for availability, authentication, context
length, output shape, latency, restrictions, cost, and automation support. Run
only harmless benchmark tasks and respect provider limits.

**Exit gate:** candidates remain isolated from production routing until adapter
tests, policy checks, and probe results meet activation thresholds.

### Chunk 5.3a: Operator probe runner

Provide a bounded non-shell command boundary for harmless candidate probes. The
wrapper receives candidate metadata and returns structured availability,
authorization, output, restriction, cost, and automation evidence. Persist
probe results, keep failed candidates quarantined, and never execute a discovered
URL directly.

**Exit gate:** `aipool candidate probe` can run a configured wrapper, enforce
timeouts and output limits, persist evidence, and leave failed candidates out of
activation.

### Chunk 5.4: Adapter generation and validation

For candidates matching known transports, generate configuration rather than
provider-specific code where possible. For new transports, create a reviewable
adapter proposal with tests. Never auto-write arbitrary executable provider code.

**Exit gate:** an operator can inspect the adapter/configuration, run its tests,
and explicitly promote a candidate to `HEALTHY`.

## Phase 6 — Semi-autonomous provider maintenance

### Chunk 6.1: Staleness diagnosis

Detect structural versus temporary failures, collect redacted diagnostics, and
compare them with documented provider/API changes. Automatically pause stale
providers rather than repeatedly probing them.

**Exit gate:** stale providers leave production routing and produce an actionable
repair report without exposing secrets.

### Chunk 6.2: Repair proposals

Allow the system to propose configuration or adapter changes only through
authorized interfaces. Run unit tests, contract tests, policy checks, and
quarantine probes before presenting promotion for approval.

**Exit gate:** no repair reaches production automatically; failed proposals are
discarded or left quarantined with evidence.

### Chunk 6.3: Operator approval workflow

Add approval records, diff inspection, rollback, expiry, and audit history for
provider activation, repair, credential changes, and policy exceptions.

**Exit gate:** every outward-facing or authorization-sensitive change has a
human-readable audit trail and a reversible rollback path.

## Phase 7 — Autonomous discovery within hard boundaries

### Chunk 7.1: Scheduled discovery and exploration

Run scheduled discovery against allowlisted sources, reserve a small exploration
budget, and route only low-risk tasks to newly validated providers. Use reputation
confidence to control exploration volume.

**Exit gate:** discovery cannot increase spend, concurrency, or external access
beyond configured budgets.

### Chunk 7.2: Continuous provider portfolio optimization

Compare providers by task-specific utility, reliability, confidence, speed,
effective cost, and operational risk. Retire or quarantine providers that stop
meeting thresholds while retaining evidence for review.

**Exit gate:** the system can explain why a provider was selected, demoted,
quarantined, or retired for a particular capability.

### Chunk 7.3: Autonomous maintenance loop

Combine discovery, probing, benchmarking, reputation updates, staleness diagnosis,
repair proposals, and rollback into a scheduled loop. Keep activation, policy
exceptions, credentials, and outward-facing changes human-approved.

**Final gate:** the pool continuously finds and evaluates legitimate compute,
improves task-specific routing from real outcomes, preserves security boundaries,
and can be stopped or rolled back without losing the audit trail.

## Cross-cutting definition of done

Every chunk must include:

- focused unit and integration tests;
- bounded timeouts, retries, concurrency, and payload sizes;
- redacted structured logs and measurable token/cost/latency outcomes;
- explicit provider authorization and policy checks;
- a failure-path test, not only a successful-provider test;
- documentation of the stable interface and any migration needed by the next chunk.

No chunk should add autonomous discovery, provider repair, or external access
before the preceding reliability, measurement, and safety gates are passing.
