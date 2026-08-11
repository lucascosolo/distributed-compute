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

## Current priority queue

1. Keep Hugging Face as the first live API transport and verify its actual free
   credit usage, limits, and task-specific quality.
2. Add a catalog-driven admin panel so the operator can configure individual API
   providers without editing environment files or exposing secrets to the repo.
3. Add guided, human-in-the-loop provider onboarding. The system may open the
   provider's official registration/key page and prepare configuration, but it
   must stop for email, phone, CAPTCHA, terms acceptance, payment, or final
   account/key creation; it must never create accounts or evade provider limits
   autonomously.
4. Remove the Discord transport completely in a dedicated cleanup chunk. Discord
   lessons remain in the ledger, but Discord code, commands, panel fields, tests,
   candidate seeds, and docs should not remain in the shipped project.

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

### Chunk 3.4: Native-vs-distributed comparison runs

Expose a bounded `aipool compare` command that runs the same synthetic cases through
an operator-supplied native-model wrapper and the coordinator. Report quality,
latency, context size, fallback rate, worker cost, and whether delegation was
actually cheaper. This is an operator-run measurement tool, not an autonomous
benchmark account or provider-registration workflow.

**Exit gate:** a controlled run produces an auditable report and refuses to call
delegation economical when its valid result is not cheaper than the native estimate.

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

TokenRouter is currently included as a model-family candidate using the operator-
supplied `https://api.tokenrouter.com/v1` endpoint. Its model IDs are provisional
metadata: live `/models` discovery and an explicit smoke test must supersede stale
catalog values. Inference.net was removed after its login path was not usable for
this deployment.

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

The candidate-aware command adapter is the initial known-transport path for
authorized Discord/Telegram wrappers; `candidate benchmark` records evidence,
but approval and routing configuration remain separate actions.

The Discord controller begins with a read-only connectivity check. Its optional
message adapter requires an operator-selected worker bot ID and never installs
other bots, uses user tokens, retries a rate limit, or rotates state to evade a
limit.

### Chunk 5.4a: Free-LLM directory ingestion

Use the public `nejib1/Free-LLM` repository as a bounded, provenance-preserving
source of API leads and metadata ideas. Normalize provider name, endpoint, model
family, transport type, free-tier category, card/verification requirements,
request/token limits, credit expiry, and source timestamp. Distinguish permanent
free tiers, renewable credits, one-time trials, and local/self-hosted tools; do
not represent any category as free forever without current evidence.

Model discovery must use a cascade: provider-reported `/models` or equivalent
metadata when available; curated source metadata when it is not; and an explicit
operator-supplied model only as a last resort. Every discovered model becomes a
separate provider profile with a capability tier and a `quota_weight` describing
its expected share of the provider's free allowance—not a monetary price.

**Exit gate:** a bounded source run produces quarantine-only API candidates with
source links and structured cost/limit metadata; stale or contradictory entries
are flagged instead of silently becoming active providers.

### Chunk 5.4b: Catalog-driven provider admin panel

Render API candidates dynamically from the checked-in catalog instead of adding a
new hard-coded form field for every service. Each provider card should show its
transport, source link, free-tier category, known limits, model field, endpoint
field when applicable, masked API-key field, enabled state, and last probe/health
state. Store provider-specific values only in the gitignored operator config with
`0600` permissions; never return secret values from `/admin/config`.

Known OpenAI-compatible entries may be wired to the generic adapter after explicit
operator enablement. Other API shapes remain visible as “adapter needed” until a
reviewable adapter exists; a key alone must never activate an untested provider.

**Exit gate:** the operator can configure two different API services from the
panel, restart the coordinator, verify both masked configurations, and route only
the provider whose smoke test and cost gate pass.

The first implementation uses one shared key per provider family and groups its
model cards together. A provider-level key must never be duplicated into separate
model secrets. Live model discovery is diagnostic first; newly returned models
need normalization, capability/quota classification, and an operator-visible
activation decision before they become cards or routing providers.

Live findings must visibly distinguish provider-reported metadata from local name
heuristics. A heuristic tier is useful for exploration but is never enough to
grant coding or high-complexity routing capability. Persist successful discovery
results as quarantined records so they remain available after restart, but require
a concise human review showing model identity, inferred capability, quota impact,
risks, and rollback before promotion into the active catalog. The operator panel
now records an authenticated approve/reject decision and review note; approval
only makes a finding eligible for a later bounded smoke test and never activates
routing by itself.

Approved API findings can now run a bounded synthetic smoke test using the shared
provider credential and quota group. Results are redacted benchmark evidence;
passing changes the finding to `smoke_tested` but does not register it for routing.
The panel then requires a second human activation note before adding the model to
the live registry; disabling it is an explicit, audited rollback that removes it
from routing without deleting its evidence.

Provider-family quota controls are now available in the panel for request limits,
token limits, and window duration. Model cards in one family share the same
persistent usage bucket, so selecting a different model cannot evade the
provider's configured free allowance.

For large work split into independent scopes, rotate eligible provider families so
different model biases contribute without provider-to-provider back-and-forth. If
independent opinions are explicitly requested, use a bounded batch of at most
three eligible providers, apply the total-call cost gate before dispatch, and
return disagreements to the native model for architecture, bug, and edge-case
review. The system should diversify only when the expected benefit justifies the
extra free-tier usage.

### Chunk 5.4c: Guided API-key onboarding

Provide a provider-specific “get a key” link and a guided checklist, not an
autonomous account-registration bot. The flow may navigate public documentation
or an official signup page, but pauses before personal-data submission, email or
phone verification, CAPTCHA, terms acceptance, payment, and final key creation.
The operator pastes or types the resulting key into the panel; the panel validates
format, stores it locally, and offers a bounded smoke test with an explicit cost
and quota warning.

**Exit gate:** onboarding reduces setup to a provider-specific, auditable sequence
without collecting credentials in logs or making accounts on the operator's
behalf.

### Chunk 5.4d: Remove Discord transport

Delete the Discord adapter, API client, CLI subcommands, panel fields, Discord
candidate seeds, Discord-specific tests, and Discord-only documentation. Retain
only generic transport, health, rate-limit, and candidate-policy behavior that is
used by other providers. Remove any Discord-specific ignored configuration from
operator-local deployment files separately; never commit or print those values.

**Exit gate:** repository search finds no Discord implementation or user-facing
configuration, the full test suite passes, the panel contains no Discord fields,
and the Hugging Face/API/browser paths remain functional.

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
