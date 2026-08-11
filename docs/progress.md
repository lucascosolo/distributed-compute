# Implementation progress

This file is the compact handoff ledger for future sessions. It records project
state, not deployment secrets. Operator-specific hosts, tokens, credentials,
database paths, and artifact roots belong only in the gitignored `.aipool.local`
or the operator's environment.

## Current checkpoint

- Branch: `main`
- Last pushed commit: `63bdba6`
- Working tree at the last checkpoint: clean
- Verification for this chunk: `160 tests passed` with
  `PYTHONPATH=src python3 -m unittest discover -s tests -q`
- VPS deployment is active; host, service, and operator configuration details remain outside the repository. The native systemd unit now points `AIPOOL_CONFIG_FILE` at its writable data directory, so the protected admin panel can persist settings without weakening the read-only project tree. The panel's `/` route redirects to `/admin`.

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
- Added BazaarLink's OpenAI-compatible free-tier candidates (`auto:free`,
  DeepSeek V4 Flash, and Qwen3.7 Flash) plus a Coze discovery lead. These remain
  quarantined; BazaarLink's documented free tier is limited and may continue at
  paid rates after quota, so no smoke test is automatic.
- Added xAI's OpenAI-compatible `grok-4.5` candidate from the official model
  documentation. Its current API pricing/free allowance must be verified for
  the operator's account before any call; it remains quarantined.
- Added authenticated `/admin/readiness`, a redacted no-network report of key
  presence, enabled/loaded state, health holds, and shared quota-window usage.
- Added source-backed quota guidance to provider cards for Hugging Face, Google
  AI Studio, Groq, OpenRouter, Mistral, and SambaNova. The panel now
  distinguishes provider quotas from optional local safety caps and leaves
  unknown values explicit.
- Added optional Cloudflare Access service-token headers to the remote client so
  a protected HTTPS gateway can be used by the CLI without an SSH tunnel. The
  current Cloudflare API token cannot create service tokens; creation remains an
  operator dashboard step until the account token is granted Access write scope.
- Operator queue commands now support local and authenticated remote
  `submit`, `status`, and `cancel` operations. The supervised worker records a
  bounded failure outcome when a coordinator invocation raises, and exits
  cleanly when its stop event is set.
- Public setup instructions use HTTPS cloning and native per-agent skill
  directories (`~/.claude/skills` or `~/.codex/skills`); CLI config discovery
  supports matching per-agent operator environment files while retaining the
  legacy shared operator path for compatibility.
- Refreshed the repository and installed distributed-compute skills with the
  VPS HTTPS handshake workflow, operator-local secret boundaries, durable queue
  usage, and explicit auth/error handling. No endpoint or credential value is
  embedded in either skill copy.
- The gateway now exposes authenticated `/admin` and `/admin/config` routes for
  allowlisted provider settings. It writes operator-local config with mode
  `0600`, never returns secret values, and makes restart required explicit.
- The admin panel now also configures the operator-owned browser wrapper used
  for authorized no-key chat candidates; the shared repository and installed
  skill both document the panel and its non-evasion constraints.
- `CommandCandidateProbe` and `aipool candidate probe` now provide a bounded
  non-shell operator workflow for testing quarantined browser/API candidates.
  Probe wrappers receive candidate metadata and return structured evidence;
  discovered URLs are never executed directly.
- Added `providers/candidate-catalog.json` and `LocalCatalogSource` so supplied
  chatbot leads can be imported reproducibly as unverified quarantine records;
  the catalog is explicitly not an active provider list.
- Added several public chatbot leads to the quarantine catalog. They remain
  unverified until terms, login behavior, capability, and rate limits are
  reviewed with a bounded synthetic probe.
- Added explicit CLI visibility and approval gates with `candidate list` and
  `candidate activate --operator-approved`. Community-platform bots
  remain candidates only when an authorized integration path is established;
  informal reachability is not treated as permission.
- The admin panel can now configure an operator-owned command worker via
  `AIPOOL_COMMAND`, which supports authorized community-platform wrappers while
  keeping the existing non-shell, rate-limit, and synthetic-test constraints.
- Approved candidates can now be benchmarked through
  `aipool candidate benchmark`; the candidate-aware command adapter passes
  metadata and task envelopes to an operator wrapper and persists capability
  observations without auto-activating the provider.
- Provider registration now validates identity, adapter shape, finite limits,
  and capability scores. `aipool.discovery` keeps sourced candidates separate
  from active adapters, rejects prohibited access/evasion language, and
  requires explicit operator approval before activation.
- Candidate records now persist in SQLite, including quarantine state and
  probe evidence. The bounded quarantine probe pipeline executes only an
  injected, operator-authorized probe, never fetches arbitrary endpoints, and
  promotes a candidate to `PROBED` only when availability, authorization,
  context, output, restrictions, cost, and automation checks all pass.
- Provider-neutral context packets now reconstruct bounded artifact-backed
  inputs with explicit untrusted-data delimiters. `BrowserChatAdapter` and
  `BrowserCommandAdapter` provide a no-API-key browser transport seam without
  embedding login, challenge bypass, or hidden-endpoint behavior.
- The comparison harness in `aipool.comparison` runs the same cases through an
  injected native-model runner and the coordinator, reporting quality,
  context-transfer size, latency, fallback, and delegation-cost differences.
- Added the operator-run `aipool compare` command. It feeds bounded synthetic
  cases to an explicitly supplied native wrapper and the coordinator, returning
  quality, latency, context, fallback, and cost evidence without registering
  accounts or providers automatically.
- Added `scripts/codex-baseline.sh` as a read-only, ephemeral Codex baseline
  wrapper. It is a template for operator-approved comparison runs and has not
  been invoked automatically or used to claim native-model benchmark results.
- Discovery requirements now include bounded public chatbot directories,
  search results, and community discussions as candidate leads, while keeping
  provenance and terms review separate from activation.
- `aipool discover` now performs one bounded Reddit search, persists
  provenance-rich leads in SQLite, deduplicates repeated sightings, and keeps
  all results out of provider activation.
- JSON-directory and RSS source adapters now normalize additional public
  directories and community feeds. `aipool candidate promote` records an
  operator's terms review and either creates a quarantined candidate or marks
  an explicitly prohibited lead rejected; it never activates a provider.
- Reddit thread ingestion now extracts a bounded, deduplicated set of external
  links from comments, so supplied discussions can feed the same lead review
  pipeline without treating comment recommendations as verified providers.
- `HtmlPageSource` and `aipool discover --page-url` now support bounded link
  extraction from public articles and directories, including the supplied
  no-signup chatbot lists; page links remain unverified leads.
- Browser transports now classify common login/sign-up walls as
  `AUTH` failures instead of returning them as successful provider output.
- `ModelGuidedBrowserAdapter` now gives the base model a bounded visible-page
  snapshot so it can choose model selectors, options, prompt fields, and submit
  buttons through typed actions. Fresh isolated browser profiles are allowed
  for clean state, but limit resets through profile rotation are explicitly not
  supported.
- Public README, provider authorization policy, and repository-copyable
  Claude/Codex skill. The installed skill is synchronized at
  `~/.agents/skills/distributed-compute/SKILL.md`.
- Hugging Face is now split into two explicit candidate paths: no-key
  `https://huggingface.co/chat/` remains a browser candidate, while the
  token-authenticated Inference Providers router is available as an optional
  `huggingface-inference` adapter using `HF_TOKEN` and a selected model. API
  rate-limit responses preserve `Retry-After` for the existing provider hold
  logic; no browser profile rotation or quota bypass is supported.
- A previously explored community-platform transport was retired after
  live testing showed bot-to-bot limitations, rate limits, and human-only
  interaction requirements. Those experiments are not part of the active
  provider registry or shipped transport surface.
- Added Character.AI and Free LLM Playground as browser-chat quarantine candidates.
  Character.AI was only inspected as a public landing page; Free LLM Playground's
  public page claims no signup/API key and a daily free cap, but neither candidate
  has been browser-tested or activated.
- Inspected `nejib1/Free-LLM` as a bounded discovery source. Its useful metadata
  model separates permanent free tiers, renewable credits, one-time trials, and
  local/self-hosted tools, while recording card requirements, rate limits, model
  IDs, and OpenAI-compatible endpoints. Added API leads such as Groq, Cerebras,
  OpenRouter, Z.AI, TokenRouter, NVIDIA NIM, Mistral, and SambaNova to the
  quarantine catalog; none is active merely because it appears in that list.
- The admin-panel chunk now expands catalog entries with model metadata into
  separate model cards. Cards show a capability tier and `quota_weight`, meaning
  expected consumption from a provider's free allowance rather than dollars;
  configured keys are shared once per provider family and remain masked; each
  model card remains quarantined until a later smoke-test/activation path.
- The provider console now shows an explicit saved/not-saved API-key badge on
  every model card. Editing a provider key defaults that provider family to
  enabled, while an explicit toggle-off remains respected. A floating save bar
  appears only after edits and uses a short plain-language human checkpoint.
  The running `serve` process now reloads its configured provider registry after
  a successful save, so adding a key does not require a manual backend restart.
- Added bounded live model discovery. The running panel successfully queried
  Hugging Face's authorized `/v1/models` endpoint and found 129 model IDs without
  exposing the token. Live results now receive conservative, explainable power,
  capability, quota-weight, and metadata-confidence hints. They are still
  diagnostic only; results are now persisted in SQLite as quarantined findings
  and shown after panel restart. They cannot become routing providers without a
  human review of identity, evidence, capability, quota impact, risks, and
  rollback.
- Discovered-model review is now an explicit authenticated workflow. A required
  human note is persisted with an approve/reject audit record; approved findings
  remain outside routing until a separate bounded smoke-test promotion step.
- Provider-family request/token/window limits are now configurable from the panel
  and applied live. Model cards sharing one provider family use one persistent
  quota bucket, while `quota_weight` still helps the router choose among models;
  zero limits mean the actual provider limit is unknown, not unlimited by claim.
- Large mapped work now rotates eligible provider families across independent
  scopes, while reusing one provider when no alternative exists. The bounded
  multi-opinion path preserves disagreements as explicitly untrusted input for
  the native fallback model, remains capped at three providers, and never creates
  provider-to-provider back-and-forth.
- Approved discovered API models now have a bounded synthetic smoke-test action.
  The adapter is constructed conservatively, benchmark evidence is persisted
  without response text or credentials, and a passing test remains non-routing
  until a later explicit activation decision.
- Activation is now a separate authenticated human decision. A passed finding
  requires an activation note before it enters the live registry; the panel also
  offers an audited disable/rollback action that removes it from routing while
  retaining its review and smoke-test evidence.
- Expanded the checked-in API candidate catalog with TokenRouter's
  OpenAI-compatible endpoint and three model-level entries. It remains
  quarantined until the operator supplies a key and runs the explicit bounded
  smoke test. Removed Inference.net after the operator could not complete login.
- Added NVIDIA NIM's official free-endpoint candidate with separate 8B and 70B
  model cards; both remain quarantined until live model discovery and a bounded
  smoke test confirm availability for the operator's key.
- Added Mistral AI and SambaNova Cloud as additional OpenAI-compatible candidate
  families, with separate general, coding, and stronger model cards. Their free
  or trial status and limits remain operator-verified metadata, not an activation
  claim.
- Added NVIDIA NIM's `z-ai/glm-5.2` and
  `nvidia/nemotron-3-ultra-550b-a55b` free-endpoint candidates as very-strong
  model cards, based on the official Build.NVIDIA catalog. They remain
  quarantine-only until live model discovery and an explicit smoke test.
- Added the operator-supplied NVIDIA NIM `muse-glimmer-30b` candidate as a
  provisional strong model entry; live `/models` discovery must confirm its
  exact identifier before it can be considered for routing.
- Retired the unsupported platform-specific transport completely: removed its
  adapter, CLI commands, panel configuration, catalog seeds, tests, setup docs,
  and ignored local configuration. Generic candidate wrappers remain available
  only for documented, authorized integrations.
- Added the repository-local `scripts/aipool` launcher so the distributed-compute
  skill works before editable package installation. The panel now exposes an
  explicit bounded smoke-test button for configured catalog models.
- Added a local `agent-command` bridge contract for Claude CLI and Codex CLI.
  Tasks carry an origin and bounded delegation chain; the coordinator excludes
  the originating runtime and all ancestor runtimes, preventing self-routing
  and Claude↔Codex ping-pong. Commands receive JSON over stdin and return only
  bounded stdout; credentials remain local. Both runtimes are opt-in through
  operator command configuration.
- Added Kilo Code as a future agent-command candidate and Kilo Gateway as a
  quarantined OpenAI-compatible candidate. Kilo's official page advertises a
  $0/forever tier with default free models, but publishes no numeric free-tier
  request/token caps; free model availability may change, so it is not yet
  treated as proven useful compute.
- Added first-class local Ollama configuration through the documented local
  OpenAI-compatible endpoint. It requires only an operator-selected model,
  defaults to loopback, and uses no real API key. Added a separate Ollama Cloud
  candidate with the current model names observed from its public `/api/tags`
  endpoint; cloud authentication, quotas, and model retirement remain distinct
  from local inference and are not assumed free.
- Corrected Ollama Cloud quota metadata after checking the provider pricing:
  the Free plan has qualitative light usage with session limits resetting every
  5 hours and weekly limits every 7 days. Usage is model-weighted rather than a
  fixed token allowance; exact account/model allowances remain unknown.
- Catalog smoke tests now require an explicit `operator_approved: true` request
  field in addition to the authenticated panel. The panel supplies that field
  only when its human operator clicks the bounded smoke-test button. This keeps
  API clients and background jobs from silently spending provider quota.
- The provider console now shows one compact card per provider family. Families
  with saved keys are marked and collapsed, unconfigured families sort first,
  and per-model controls remain behind a `View model details` button. The shared
  family key field still enables all models in that family when saved.

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
5. Every public chatbot may be recorded as a candidate, but an explicitly
   documented, legally binding prohibition on the intended external use keeps
   it out of activation. Absence of such a prohibition is not a substitute for
   operator review of applicable law, privacy, safety, and rate limits.
6. Never bypass authentication, paywalls, CAPTCHAs, quotas, rate limits,
   safeguards, or provider Terms of Service.

## Next scoped chunk

Continue researching and normalizing official quota rules for the remaining
provider families before running real smoke tests. The first pass now covers
seven families plus Kilo's qualitative free-tier claim; the next pass should add evidence where the provider publishes
it, preserve account-dependent dimensions, and avoid guessing unknown caps.
The smoke-test checklist is in `docs/provider-smoke-test-plan.md`. The operator
has approved running a bounded smoke test when the implementation is ready;
show the exact selected batch and expected quota impact before spending calls.
Then wire the native-agent bridge wrappers and run the approved bounded
cross-runtime test. After that, obtain a Cloudflare Access service token through the operator
dashboard if CLI-over-HTTPS use is needed, then run a human-approved bounded
provider benchmark. Retired transports are not reintroduced.

The skill refresh is complete; keep it synchronized whenever the remote gateway
handshake or CLI contract changes.

The VPS service is already deployed behind the operator's Cloudflare HTTPS
hostname. Do not put a real VPS address, service token, or provider credential in
the repository.

## Verification command

```bash
PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests -q
```
