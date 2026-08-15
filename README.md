# Distributed AI Compute Coordinator

**Cost-aware, capability-aware AI workload orchestration for Claude Code, Codex, and other agent stacks.**

Distributed Compute (`aipool`) routes small, separable AI tasks to a pool of
legitimately accessible providers: local models, command-line workers,
OpenAI-compatible APIs, and future authorized adapters. It benchmarks providers
by capability instead of treating every model as equally useful, validates
untrusted worker output, tracks health and savings, and falls back to the native
model when delegation is unsafe, unsupported, or more expensive than doing the
work directly.

Search terms: distributed AI compute, AI workload orchestration, multi-provider
LLM routing, cost-aware model routing, cheap AI task delegation, free AI compute,
provider capability benchmarks, Claude Code delegation, Codex integration,
local LLM orchestration, and heterogeneous model routing.

> **Project status:** early local MVP. The core contracts, adapters, routing,
> validation, persistence, capability probing, verification, consensus, map, and
> map-reduce paths are implemented. Automatic provider discovery and production
> VPS operation remain roadmap work. Do not
> treat this repository as production-ready infrastructure yet.

## Why this project exists

Delegating work is only useful when the delegated result costs less than the
primary model's own context and compute. `aipool` therefore refuses delegation
when it cannot establish a positive savings estimate. Capability is a separate
gate: a free helper that can classify text is not automatically allowed to write
code, review a repository, plan an architecture, or handle sensitive work.

For larger routine jobs, a trusted native model can provide explicit bounded
scopes. The coordinator can then map those scopes to simple subtasks and reduce
their results without inventing complex implementation instructions for weak
providers.

## Current capabilities

- Standard provider adapters for fixtures, local commands, OpenAI-compatible
  APIs, and an injected browser-chat transport for web chat interfaces whose
  reviewed terms do not explicitly prohibit the intended external use.
- Cost-aware routing across classification, extraction, summarization, coding,
  review, research, and related capability requirements.
- Deterministic quality gates for empty, boilerplate, refusal-only, malformed,
  truncated, and task-inconsistent output.
- Persistent SQLite outcomes, cache entries, provider observations, health, and
  approximate orchestration savings.
- Capability benchmarks with durable empirical scores, including discovery of
  capabilities not present in a provider's initial declaration.
- Bounded `verify` and three-provider `consensus` strategies.
- Explicit-scope `map` and bounded `map_reduce` strategies.
- Durable, bounded queueing with idempotent enqueue, inspection, cancellation,
  expiring worker leases, and a supervised background worker.
- Policy-first candidate quarantine that requires documented authorization and
  explicit operator approval before a discovered source can be activated.
- Native-model fallback when no capable provider exists, quality validation
  fails, a composite subtask cannot be delegated, or delegation is not cheaper.
- Content-addressed artifact storage and a concise Claude/Codex skill under
  [`skills/distributed-compute`](skills/distributed-compute/SKILL.md).
- Bounded, provider-neutral context packets that reconstruct artifact-backed
  task context for constrained API or browser transports without passing
  credentials or treating source text as coordinator instructions.
- Model-guided browser sessions that use the native model to select visible
  model/options controls and submit a task through a bounded typed action plan.
- Bounded Reddit lead discovery with persistent provenance; discovered links
  remain leads/candidates and are never activated automatically.
- JSON-directory and RSS lead adapters plus an explicit lead-to-quarantine
  promotion command.

## Quick start: run everything on one computer

The current MVP is a local Python package. It assumes Python 3.13 or newer.

```bash
git clone https://github.com/lucascosolo/distributed-compute.git
cd distributed-compute
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .aipool.local.example .aipool.local
```

`.aipool.local` is ignored by Git. Put operator-specific paths, provider
endpoints, and credentials there; never commit them. The example file contains
placeholders only.

For a deterministic smoke test, configure a fixture provider in `.aipool.local`:

```dotenv
AIPOOL_FIXTURE_OUTPUT={"label":"docs"}
AIPOOL_DB=.aipool-data/aipool.sqlite
```

Then submit a task with a local-work estimate larger than the coordinator's
estimated delegation cost:

```bash
aipool task --db .aipool-data/aipool.sqlite --json \
  '{"task":"classification","input_ref":"artifact:example","requirements":{"output":"json"},"local_estimate":1.0}'
```

Useful inspection commands:

```bash
aipool providers
aipool status
aipool stats --db .aipool-data/aipool.sqlite
```

To collect a small, persisted batch of public chatbot leads from Reddit:

```bash
aipool discover --db .aipool-data/aipool.sqlite \
  --query "free chatbot no API key" --max-results 10
```

For a specific discussion thread, extract only bounded external links from its
comments:

```bash
aipool discover --db .aipool-data/aipool.sqlite \
  --thread-url "https://www.reddit.com/r/ChatGPT/comments/195buk4/ai_chatbots_or_other_services_that_dont_require_a/" \
  --max-results 25
```

Article and directory pages can also be scanned for bounded external links:

```bash
aipool discover --db .aipool-data/aipool.sqlite \
  --page-url "https://techtactician.com/best-free-ai-chat-tools-no-sign-up/" \
  --max-results 25
```

Page links are discovery leads only. Navigation links, recommendations, and
marketing claims are not evidence that a chatbot is available, capable, or
permitted to automate.

The repository also contains `providers/candidate-catalog.json`, a
quarantine-only seed list assembled from operator-supplied leads. Import it
into local discovery state with:

```bash
aipool discover --catalog-file providers/candidate-catalog.json \
  --db .aipool-data/aipool.sqlite
```

These entries are not default active providers. They must pass current terms,
login-wall, capability, output, cost, and rate-limit checks before promotion.

This performs one bounded public search request. It does not probe, automate,
or activate any discovered chatbot; review each source's current terms and
candidate evidence before configuring a transport.

After reviewing a lead, promote it into the quarantined candidate registry:

```bash
aipool candidate promote LEAD_ID --db .aipool-data/aipool.sqlite \
  --terms-review "Reviewed date: no explicit binding prohibition found"
```

Use `--terms-prohibited` when the reviewed terms contain an explicit binding
prohibition on the intended external use. Promotion never activates a provider;
successful probes and separate operator approval are still required.

Community-platform candidates follow the same rule: being informal or
publicly reachable is not permission to automate or send private data. Keep
these candidates quarantined unless the platform, owner, and applicable rules
explicitly authorize the integration.

Inspect candidate state with `aipool candidate list`; approve a passing probe
only with `aipool candidate activate ID --operator-approved`. Approval records
operator review but does not create credentials or bypass platform controls.

After approval, run the bounded capability cases through an operator-owned
candidate wrapper with `aipool candidate benchmark ID --command '...'`. The
wrapper receives candidate metadata plus each task envelope on stdin and must
return only the worker text on stdout; benchmark scores are persisted as
provider observations.

For asynchronous work, enqueue a task and inspect it later:

```bash
aipool queue submit --db .aipool-data/aipool.sqlite --json \
  '{"task":"classification","input_ref":"artifact:example","local_estimate":1.0}'
aipool queue status --db .aipool-data/aipool.sqlite TASK_ID
aipool queue cancel --db .aipool-data/aipool.sqlite TASK_ID
```

To run the HTTP gateway on the same computer, keep it loopback-only by default:

```dotenv
AIPOOL_HOST=127.0.0.1
AIPOOL_PORT=8765
AIPOOL_TOKEN=
```

```bash
aipool serve --db .aipool-data/aipool.sqlite
```

The gateway requires a bearer token before it will bind to a non-loopback host.
For a VPS or another authorized machine, install the project there, configure
that machine's ignored `.aipool.local` with `AIPOOL_HOST`, `AIPOOL_PORT`,
`AIPOOL_TOKEN`, `AIPOOL_DB`, and its provider settings, then run `aipool serve`.
On the client machine, use only operator-local configuration:

```dotenv
AIPOOL_MODE=remote
AIPOOL_BASE_URL=http://127.0.0.1:8765
AIPOOL_TOKEN=replace-with-the-same-operator-chosen-token
```

Change `AIPOOL_BASE_URL` to the authorized server address only in that ignored
client config. Prefer a private network or authenticated tunnel; do not expose
the gateway directly to the public Internet without an additional access
control layer.

The CLI returns machine-readable JSON. A result containing
`"native_fallback":true` and `"success":false` is an intentional handoff: let the
native Claude or Codex session finish that task rather than repeatedly retrying the
same request through the pool. The pool did not produce a delegated answer.

## Configuring legitimate workers

The CLI currently supports these local configuration paths:

| Provider | Configuration | Notes |
| --- | --- | --- |
| Fixture | `AIPOOL_FIXTURE_OUTPUT` | Deterministic smoke tests only. |
| Local command | `AIPOOL_COMMAND` | Receives one JSON task envelope on stdin; shell execution is disabled. |
| OpenAI-compatible API | `AIPOOL_OPENAI_ENDPOINT`, `AIPOOL_OPENAI_MODEL`, `AIPOOL_OPENAI_API_KEY` | Use only an endpoint and account you are authorized to automate. |
| Hugging Face Inference Providers | `AIPOOL_HF_MODEL`, `HF_TOKEN`, optional `AIPOOL_HF_ENDPOINT` | Token-authenticated API route; separate from the no-key HuggingChat browser candidate. |
| Browser chat wrapper | `AIPOOL_BROWSER_COMMAND` | An operator-supplied command reads the bounded rendered prompt on stdin and writes the chatbot text response; no API key is required by `aipool`. |

The coordinator keeps provider-specific behavior inside adapters. A provider
profile declares capabilities and complexity limits, then benchmark and
production evidence adjusts the routing score. No provider is trusted merely
because it is free, popular, or fast.

When the gateway is running, open `/admin` to configure model-specific API cards
from the checked-in quarantine catalog. Each model is shown as its own provider
with a capability tier and `quota_weight`—expected consumption from that
provider's free allowance, not a dollar price. OpenAI-compatible cards can be
enabled after review; other API shapes remain marked as requiring an adapter.
The panel requires the gateway bearer token on remote bindings, never displays
existing secret values, writes the gitignored `.aipool.local` file with mode
`0600`, and requires a coordinator restart before changes take effect. Provider
model discovery will prefer a live `/models` response, then curated catalog
metadata, then an explicit operator-supplied model.

Quarantined candidates can be tested with `aipool candidate probe`. Set
`AIPOOL_CANDIDATE_PROBE_COMMAND` (or pass `--probe-command`) to an
operator-owned, non-shell wrapper. It receives one candidate JSON object on
stdin and must return one `ProbeResult` JSON object on stdout. The wrapper may
use a browser and a native planner to operate visible controls, but it must
not log in, bypass challenges, evade limits, or call hidden endpoints.

## Claude and Codex integration

The reusable skill is included in the repository so other users can import it.
For Claude Code, copy it into Claude's normal per-user skills directory:

```bash
mkdir -p ~/.claude/skills/distributed-compute
cp skills/distributed-compute/SKILL.md ~/.claude/skills/distributed-compute/SKILL.md
```

For Codex, use its configured skills directory (normally `~/.codex/skills`):

```bash
mkdir -p ~/.codex/skills/distributed-compute
cp skills/distributed-compute/SKILL.md ~/.codex/skills/distributed-compute/SKILL.md
```

The skill deliberately tells the primary agent to retain responsibility for
security, architecture, ambiguous debugging, credentials, production changes,
and final synthesis. It also explains how to locate the ignored operator config.

## VPS and remote deployment

The repository reserves `.aipool.local` for deployment-specific values such as
the coordinator host, database path, artifact root, and authentication token.
Those values must never appear in tracked files, documentation, tests, or skill
files. The standard-library gateway, durable queue, worker supervision, and
thin remote client are available now; TLS termination and the production VPS
deployment workflow remain roadmap work. Do not infer a production VPS address
or token from this repository. A generic, placeholder-only systemd example is in
[`deploy/aipool.service.example`](deploy/aipool.service.example).

## Responsible use and provider terms

Use this project only with compute sources that you are authorized to access and
automate. Read and follow the individual Terms of Service, acceptable-use rules,
API documentation, rate limits, privacy terms, and licensing conditions for
every provider, model, website, account, and endpoint you configure.

Do **not** use this project to bypass authentication, paywalls, CAPTCHAs, quotas,
rate limits, access controls, usage restrictions, or provider safeguards. Do not
use stolen credentials, stolen sessions, hidden endpoints, or unauthorized
browser automation. If a provider does not permit the intended automation, do
not add or use an adapter for it. The provider policy in
[`docs/provider-policy.md`](docs/provider-policy.md) is part of the project
boundary, not a way to override a provider's rules.

You are responsible for the providers you select, the data you send, the tasks
you delegate, and your compliance with applicable law and provider terms. The
authors and contributors do not control third-party services and are not
responsible for a user's decision to use this project in a way that violates a
provider's rules, contract, privacy obligations, or law. This is an engineering
disclaimer, not legal advice. Never send secrets, personal data, credentials, or
irreversible instructions to an untrusted worker.

## Development

Run the full test suite from the repository root:

```bash
PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests -q
```

The project is intentionally dependency-light and uses the Python standard
library for the coordinator MVP. Read [`project.md`](project.md) for the full
architecture and [`roadmap.md`](roadmap.md) for the staged path from local MVP
to controlled provider discovery and VPS operation.

## Contributing

Small, verifiable changes are preferred. New adapters must document their
authorization assumptions, enforce bounded time/output behavior, normalize
errors, and include tests. Changes to routing must preserve both capability
gates and the native cost gate. Never add real credentials, private endpoints,
deployment addresses, or provider data to a pull request.
