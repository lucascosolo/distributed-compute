# Implementation progress

This file is the compact handoff ledger for future sessions. It records project
state, not deployment secrets. Operator-specific hosts, tokens, credentials,
database paths, and artifact roots belong only in the gitignored `.aipool.local`
or the operator's environment.

## Current checkpoint

- Branch: `main`
- Last pushed commit: `1cbf64d`
- Working tree at the last checkpoint: clean
- Verification for this chunk: `148 tests passed` with
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
- Added several current Discord-directory chatbot leads to that same quarantine
  catalog. They remain unverified until the operator reviews terms, invites
  them manually, and the bounded Discord benchmark records usable output.
- Added explicit CLI visibility and approval gates with `candidate list` and
  `candidate activate --operator-approved`. Community Telegram/Discord bots
  remain candidates only when an authorized integration path is established;
  informal reachability is not treated as permission.
- The admin panel can now configure an operator-owned command worker via
  `AIPOOL_COMMAND`, which supports authorized Discord/Telegram wrappers while
  keeping the existing non-shell, rate-limit, and synthetic-test constraints.
- Approved candidates can now be benchmarked through
  `aipool candidate benchmark`; the candidate-aware command adapter passes
  metadata and task envelopes to an operator wrapper and persists capability
  observations without auto-activating the provider.
- The secure panel now has Discord controller fields for application, guild,
  channel, and masked bot-token configuration, with a least-privilege setup
  documented for a private synthetic test server.
- Added `DiscordApiClient` and `aipool discord check`, a read-only bot API
  health check. The configured controller successfully identified itself and
  accessed the configured server/channel during live verification; it has not
  sent messages or installed other bots.
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
- Discord now has a guarded worker adapter: after a read-only setup check, an
  worker sends one bounded task envelope to the configured channel, polls only
  after its own message for the selected bot's reply, and maps authentication,
  rate-limit, network, and timeout failures into the normal provider result/hold
  path. It never installs bots, uses user tokens, or retries a 429 automatically.
- Discord worker discovery now lists bot members from the configured guild and
  creates one low-complexity provider per bot automatically, excluding the
  controller. No per-worker IDs are stored in operator configuration. The
  Discord Developer Portal must enable Server Members Intent for enumeration and
  Message Content Intent for ordinary reply content.
- `aipool discord benchmark` now runs the existing three-case bounded benchmark
  sequentially across up to three discovered worker bots, persists per-bot
  capability observations, and sends failures through the normal health hold
  logic. It requires no per-worker IDs.
- Discord task messages now render the shared bounded `ContextPacket`, including
  configured artifact contents when available, instead of sending an opaque
  task reference that a remote worker could not reconstruct. Context remains
  explicitly untrusted and is truncated to the Discord message budget.
- The built-in Discord benchmark cases now contain explicit synthetic objectives
  and input text, so a response is tested against a reconstructable task rather
  than an empty `benchmark:*` reference.
- Discord workers are now mentioned automatically before each bounded task,
  which removes another per-bot configuration requirement for common
  mention-triggered bots; the optional prefix remains available for nonstandard
  command formats.
- Automatically discovered Discord workers now begin in `QUARANTINED` state;
  only the bounded benchmark can establish health and make their observed
  capabilities eligible for routing.
- Discord benchmarking now stops immediately on authentication or rate-limit
  responses and preserves `Retry-After` for the provider health hold, preventing
  the benchmark itself from consuming a limited worker's remaining quota.
- First live Discord run found `CommunityOne` and `Quickchat AI`. Both returned
  rate-limit behavior before producing a valid benchmark result; the database
  recorded their observations and health holds, and a follow-up task returned
  the required native fallback (`no_healthy_capable_provider`). The benchmark
  now reports held/degraded workers as skipped rather than probing them again.
- A Discord rate-limit now blocks immediate retries against other Discord
  workers during the same routed task and stops the remainder of a benchmark
  batch. Other provider transports can still be considered when available.
- Operator verification showed `CommunityOne` responds to a human-authored
  prompt but not to the controller bot's prompt. It is therefore a
  `bot_to_bot_unsupported` candidate, not usable Discord compute; do not keep
  retrying it or attempt to solve that limitation with user OAuth.
- Added the read-only `aipool discord recent` diagnostic. The live channel
  confirmed the human-vs-bot distinction, and CommunityOne was marked disabled
  in the ignored local database with the reason
  `bot_to_bot_unsupported`; no repository secret or account OAuth was used.
- Live discovery later found `Hana`. The operator confirmed that it requires the
  user-invoked `/ask-hana` slash command rather than responding to a bot mention.
  Discord application commands are user-invoked interactions; the controller must
  not impersonate a user or use account OAuth to trigger one. Added
  `aipool discord hold --username ... --reason ...` so this evidence can be
  recorded without another probe; Hana remains held locally and is not compute.
- A bounded benchmark of the newly discovered `Learning LLM` bot sent the three
  synthetic cases but received no usable response (`valid: 0/3`). Its persistent
  state is now `degraded`, and the panel was restarted and verified with all four
  discovered workers represented: two disabled and two degraded. No Discord worker
  currently qualifies for routing.
- Discord is paused as an active transport. Hugging Face Inference Providers is
  configured only in the ignored operator config with model
  `Qwen/Qwen3-8B:cheapest`; a bounded live classification smoke test returned valid
  JSON, used 710 reported worker tokens, and produced an internal delegation cost
  of `0.08` versus a local estimate of `1.0`. This proves the adapter works, but
  does not prove the request was free; Hugging Face billing/credit status remains
  an operator concern and the router's `:cheapest` policy is not a free guarantee.
- Added Character.AI and Free LLM Playground as browser-chat quarantine candidates.
  Character.AI was only inspected as a public landing page; Free LLM Playground's
  public page claims no signup/API key and a daily free cap, but neither candidate
  has been browser-tested or activated.
- Inspected `nejib1/Free-LLM` as a bounded discovery source. Its useful metadata
  model separates permanent free tiers, renewable credits, one-time trials, and
  local/self-hosted tools, while recording card requirements, rate limits, model
  IDs, and OpenAI-compatible endpoints. Added API leads such as Groq, Cerebras,
  OpenRouter, Z.AI, and Inference.net to the quarantine catalog; none is active
  merely because it appears in that list.
- The operator requested removing Discord entirely. That removal is recorded as
  roadmap chunk 5.4d rather than being mixed into the current provider pivot;
  no Discord code has been deleted in this checkpoint.
- The admin-panel chunk now expands catalog entries with model metadata into
  separate model cards. Cards show a capability tier and `quota_weight`, meaning
  expected consumption from a provider's free allowance rather than dollars;
  configured keys are shared once per provider family and remain masked; each
  model card remains quarantined until a later smoke-test/activation path.
- Added bounded live model discovery. The running panel successfully queried
  Hugging Face's authorized `/v1/models` endpoint and found 128 model IDs without
  exposing the token. Live results are currently diagnostic; hydrating new model
  cards and assigning capability/quota metadata remains a follow-up step.

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

Continue with the configured Hugging Face API and bounded browser/API-candidate
probes. Verify actual cost/credit behavior before treating Hugging Face as free,
and keep candidates quarantined until a probe proves usable context transfer,
valid output, acceptable terms, and a cheaper total cost. The next implementation
chunk is live model-list discovery and quota accounting; Discord removal follows
as roadmap chunk 5.4d.

Only after those checks consider a VPS deployment using the deploy skill. Do not
put a real VPS address or token in the repository.

## Verification command

```bash
PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests -q
```
