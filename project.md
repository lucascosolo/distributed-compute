# Goal: Build a Distributed AI Compute Layer for My Agent Stack

Build a practical distributed AI compute system that lets Claude CLI, Codex CLI, and other agents in my existing stack delegate appropriate work to a pool of cheap/free AI providers.

Use projects like `cyberpapiii/chipotlai-max` as architectural inspiration for provider adapters and OpenAI-compatible abstraction, but do **not** rely on stolen compute, bypass authentication/paywalls/rate limits, evade provider restrictions, or automate services where doing so is prohibited. Providers must be legitimately accessible through public APIs, free tiers, local models, user-authorized sessions, or web interfaces that permit the intended automation.

## Existing environment

I already have Claude CLI and Codex CLI configured around a shared:

`~/.agents/`

directory containing common:

- system instructions
- skills
- tools
- agent configuration

This project should integrate cleanly with that architecture rather than replacing it.

I also have a VPS that can host the persistent coordinator, provider registry, benchmarking system, API gateway, health monitoring, and related services.

## Desired user experience

From Claude or Codex, I want delegation to feel approximately like:

`delegate this routine repository scan`
`ask cheap workers to classify these files`
`parallelize these independent research/evaluation tasks`
`use the cheapest capable provider`
`get three independent opinions`

The calling agent should not need to know which provider performs the work.

Expose a small, stable interface through `~/.agents/skills/` and/or MCP, CLI, HTTP, or another appropriate mechanism.

The coordinator should handle provider selection internally.

## Core architecture

Design the system around several layers:

### 1. Provider adapters

Every provider implements one standard interface.

Conceptually:

`Provider`
- id
- name
- transport
- available models/agents
- capabilities
- context limits
- latency
- reliability
- rate limits
- concurrency limits
- estimated cost
- authorization requirements
- health state
- last successful request
- adapter version

Possible transports may include:

- OpenAI-compatible APIs
- legitimate free APIs
- local model servers
- Ollama/llama.cpp/etc.
- user-authorized web interfaces when automation is permitted
- CLI-based models
- future provider types

Provider-specific ugliness must remain inside its adapter.

The rest of the system should never care whether the worker is an API, local model, CLI, or permitted browser-backed assistant.

### 2. Capability registry

Do not rank models using one simplistic global leaderboard.

Track capabilities separately, such as:

- classification
- extraction
- summarization
- basic reasoning
- coding
- code review
- repository scanning
- debugging
- research
- planning
- instruction following
- structured JSON output
- long-context comprehension
- tool use
- mathematics
- creative generation

Maintain empirical scores based on actual completed work.

A weak model might therefore be:

excellent at classification,
acceptable at extraction,
poor at coding,
terrible at complex planning.

That distinction is important.

### 3. Task router

Before delegating, classify the task cheaply.

Estimate:

- task type
- complexity
- required capabilities
- expected input/output size
- importance
- acceptable failure risk
- parallelizability

Then choose the **least expensive sufficiently capable worker**, rather than automatically selecting the strongest model.

Escalate upward only when necessary.

Example:

filesystem inventory → tiny/free model

simple classification → tiny/free model

summarization → small capable model

routine code generation → medium model

architecture/security/ambiguous debugging → strong model

critical final synthesis → Claude/Codex/current primary agent

The paid orchestrator should primarily make routing decisions and synthesize results, not redo all delegated work.

## Critical requirement: orchestration must save tokens

This system is pointless if Claude/Codex spends 5,000 tokens explaining a task to a free model that could have been performed directly in 2,000.

Make token economics a first-class feature.

Prefer:

- compact machine-readable task envelopes
- references to files instead of copying them
- hashes/cache keys
- shared artifact storage
- short provider prompts
- structured responses
- result compression
- batching
- deterministic preprocessing in ordinary code
- avoiding LLMs entirely when grep/AST/parser/scripts can solve something

Track approximately:

`orchestration_cost`
`delegated_compute_saved`
`paid_tokens_consumed`
`worker_tokens_consumed`
`latency`
`success`

The router should refuse delegation when delegation is predicted to cost more than doing the task locally with the primary model.

## Task envelopes

Design a minimal protocol along the lines of:

```json
{
  "task": "classify",
  "input_ref": "...",
  "requirements": {
    "output": "json",
    "confidence": true
  }
}
```

Do not send giant natural-language agent prompts to workers unless necessary.

Workers should receive only the information needed for their specific subtask.

## Response quality gate

Free assistants frequently produce responses that are syntactically valid but useless.

Build an inexpensive validation layer.

Detect results such as:

- generic assistant introductions
- "I am the assistant for COMPANY..."
- customer-support boilerplate
- refusal without performing the task
- irrelevant marketing text
- navigation/help menus
- empty output
- malformed structured data
- obvious truncation
- repeated garbage
- responses unrelated to the requested task

Do NOT automatically treat every refusal as malicious or try to bypass legitimate provider restrictions.

Instead classify the result as unusable for that task and route elsewhere.

Use cheap deterministic filters first.

Only use an LLM judge when deterministic validation cannot determine whether the response is useful.

Record failures against the provider's capability/reliability score.

## Provider health management

Implement provider states such as:

HEALTHY
DEGRADED
RATE_LIMITED
AUTH_REQUIRED
BROKEN
QUARANTINED
DISABLED

Run lightweight health probes periodically.

Use exponential backoff.

Do not repeatedly hammer broken providers.

Track:

- last success
- last failure
- failure reason
- rolling success rate
- latency
- output validity
- recent capability scores

Providers that stop working should automatically leave the active routing pool.

## Stale provider repair

Adapters will inevitably break.

Separate provider definitions from the core system.

When a provider becomes stale:

1. identify whether the failure appears temporary or structural
2. collect diagnostic information
3. look for documented API/interface changes when appropriate
4. propose or attempt an adapter update only through authorized interfaces
5. run adapter tests
6. restore it only after validation

Never implement CAPTCHA bypassing, authentication bypassing, credential theft, rate-limit evasion, hidden endpoint abuse, or other mechanisms intended to defeat provider controls.

If legitimate automated access is no longer available, disable the provider.

## Automatic provider discovery

Design an extensible discovery mechanism so new legitimate free/cheap compute sources can be added over time.

Potential discovery sources include:

- open model hosting services
- providers announcing free tiers
- public inference APIs
- local/open-source models
- officially exposed AI tools
- services intentionally offering developer access
- public chatbot directories and search results
- community discussions and recommendations (including Reddit and similar
  public forums)
- public websites exposing task-oriented assistants, such as shopping,
  support, travel, or coding helpers

Community posts, search results, and public pages are discovery leads only.
The discovery runner must use bounded, rate-limited retrieval through a
permitted source method, preserve the source URL and timestamp, deduplicate
leads, and never treat a recommendation as proof that a chatbot may be
automated. Each lead still becomes a quarantined candidate and is excluded when
the operator's terms review finds an explicit legally binding prohibition on
the intended external use.

Discovery should produce **candidate providers**, not immediately activate them.

Candidates enter a quarantine/testing pipeline.

Test them for:

- availability
- authorization requirements
- capability
- context length
- output quality
- reliability
- latency
- restrictions
- cost
- automation/API availability

Only validated providers enter the production pool.

## Benchmarking

Create a compact benchmark suite.

Do not burn huge amounts of inference continuously.

Use small representative tests covering capabilities like:

- extraction
- classification
- summarization
- code explanation
- small coding task
- reasoning
- instruction following
- JSON formatting

Maintain rolling scores from both benchmarks and actual production tasks.

Production outcomes should eventually carry more weight than synthetic benchmarks.

## Reputation

Provider selection should consider something like:

`utility = capability × reliability × confidence × speed / effective_cost`

Do not hard-code this exact equation if a better system makes sense.

Support exploration occasionally so newly added providers get enough jobs to establish a reputation.

Do not repeatedly send important tasks to unknown providers.

## Verification strategies

For cheap/free compute, redundant verification can sometimes be worthwhile.

Support strategies such as:

`single`
Use one worker.

`verify`
One worker performs the task and another checks it.

`consensus`
Several inexpensive workers independently answer and results are compared.

`map`
Split independent pieces across workers.

`map_reduce`
Workers handle pieces and a stronger model synthesizes.

`cascade`
Start cheap and escalate if confidence/validation is poor.

The router chooses an appropriate strategy based on task importance and available compute.

## Security

Treat provider output as untrusted input.

A provider must never be able to inject instructions into the coordinator simply by returning text.

Separate:

DATA FROM PROVIDER

from:

INSTRUCTIONS TO ORCHESTRATOR

Provider responses must not gain tool permissions.

Sanitize and validate structured responses.

Do not expose secrets unnecessarily.

Use narrowly scoped credentials.

Keep credentials outside repositories.

Implement logging that avoids leaking secrets or sensitive prompt contents.

## Shared artifact system

Avoid repeatedly transmitting enormous files through model contexts.

Create an artifact store where tasks can reference:

- files
- repository snapshots
- command output
- extracted text
- AST results
- prior worker output

Workers should receive slices of artifacts where possible.

The coordinator should pass references rather than duplicating data internally.

## Caching

Aggressively cache safe deterministic or semideterministic work.

Cache keys should consider:

- task type
- normalized prompt
- input artifact hashes
- provider/model
- relevant configuration

Examples:

Do not summarize an unchanged file twice.

Do not reclassify an unchanged repository repeatedly.

Do not rerun provider capability probes unnecessarily.

## Local compute

Treat local models as first-class providers.

If my hardware can perform an easy task locally for essentially zero marginal cost, prefer that over an external service where reasonable.

Leave clean interfaces for adding additional machines later.

The architecture should eventually support multiple compute nodes, not just the VPS.

## VPS architecture

The VPS should host the persistent control plane.

Consider components such as:

`gateway`
Stable interface used by Claude/Codex.

`router`
Chooses execution strategy/provider.

`provider-registry`
Stores capabilities, health and configuration.

`worker/adapters`
Communicate with providers.

`benchmark-service`
Evaluates workers.

`artifact-store`
Stores shared task input/output.

`queue`
Handles parallel/background jobs.

`health-monitor`
Detects failures.

`metrics`
Tracks effectiveness and cost savings.

Do not create microservices merely for aesthetic reasons. A modular monolith is preferable initially if it substantially reduces operational complexity.

## Integration with ~/.agents

Create a clean integration rather than stuffing implementation details into system instructions.

Something approximately like:

```text
~/.agents/
  skills/
    distributed-compute/
      SKILL.md
      ...
```

The skill should teach Claude/Codex:

WHEN delegation is worthwhile,
WHAT kinds of tasks should be delegated,
HOW to call the coordinator,
WHEN not to delegate,
WHEN results require verification,
and HOW to avoid wasting primary-model tokens.

Keep the skill extremely concise because it may frequently enter agent context.

Heavy documentation belongs in the project repository, not global system instructions.

## CLI

Create a useful administration CLI.

Example direction:

```bash
aipool status
aipool providers
aipool providers inspect <id>
aipool benchmark <id>
aipool disable <id>
aipool enable <id>
aipool task ...
aipool stats
aipool savings
```

Exact naming is open to improvement.

## Observability

I want to be able to answer:

Which workers are being used?

What are they good at?

Which ones are failing?

How much free/local work have they performed?

How many paid orchestrator tokens did delegation consume?

How much work was avoided on expensive models?

Which providers are degrading?

Which task classes benefit from delegation?

Which task classes are actually more expensive when delegated?

Build metrics around those questions.

## Important architectural principle

The system should progressively learn:

> "Who is the cheapest worker I can trust for this specific kind of task?"

rather than:

> "What is the strongest AI currently available?"

## Development approach

Do not attempt to build the entire grand vision at once.

First inspect my environment and existing `~/.agents` architecture.

Then create concise project context/documentation and an implementation plan.

Build a narrow vertical slice:

Claude/Codex
→ shared distributed-compute skill/tool
→ coordinator
→ task classifier/router
→ 2-3 legitimate provider adapters
→ response validator
→ capability/reliability registry
→ result returned to caller

One provider can be local.

One can be an API/OpenAI-compatible provider.

A third can demonstrate another legitimate adapter type if readily available.

Once this end-to-end path works reliably, add benchmarking, health management, caching, artifact references, more sophisticated routing, and provider discovery.

## Testing requirement

Do not merely mock the architecture.

Actually demonstrate:

1. Claude/Codex can submit a task.
2. The coordinator selects an appropriate worker.
3. The worker performs the task.
4. Garbage/refusal output is detected.
5. Failed providers are retried or bypassed appropriately.
6. Capability/reliability information changes from observed performance.
7. A cheap/simple task avoids consuming significant primary-model context.
8. A difficult task escalates to a stronger model.
9. An unavailable provider is removed from routing.
10. The final interface remains simple from Claude/Codex's perspective.

Measure the token overhead of delegation during these tests.

## First actions

Start by examining:

1. my existing `~/.agents/` layout
2. Claude CLI integration opportunities
3. Codex CLI integration opportunities
4. what shared tool/MCP/CLI mechanism provides the cleanest interface
5. what should run locally versus on the VPS

Then write a concise architecture document and repository skeleton.

Do not spend a long time speculating before producing a working vertical slice.

Favor simple interfaces, strong boundaries, measurable behavior, and minimal token overhead.

The end state should feel like I have an invisible pool of heterogeneous AI workers behind Claude and Codex, with the primary agent automatically reserving expensive intelligence for the parts of a problem that actually require it.
