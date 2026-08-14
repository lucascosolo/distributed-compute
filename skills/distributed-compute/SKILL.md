---
name: distributed-compute
description: Automatically delegate cheap, bounded, low-risk work to the configured aipool coordinator when it saves primary-model context.
---

# Distributed Compute

Use this capability automatically whenever the current task contains routine,
separable work such as inventory, classification, extraction, small summaries,
independent opinions, or bounded repository scans. Do not ask the operator for
permission before an eligible delegation: the operator has authorized this
bounded coordinator path. Do not delegate secrets, irreversible actions, tool
permissions, or work whose compact delegation envelope costs more than doing it
directly.

Delegate when the work is low-risk and separable, the input fits a bounded
artifact reference or compact envelope, and the estimated coordinator cost is
lower than native context and compute cost. Keep final synthesis, ambiguous
reasoning, security decisions, file changes, and external effects native.

When delegation is appropriate, invoke the launcher directly and continue with
the result. If the coordinator is unavailable, unauthenticated, uneconomical,
or returns `native_fallback`, continue natively without asking the operator to
repair configuration during the task.

The launcher discovers operator configuration automatically. It checks the
repository `.aipool.local`, the matching per-agent file
(`~/.claude/distributed-compute.env` or `~/.codex/distributed-compute.env`),
and the shared `~/.agents/distributed-compute.env`; `AIPOOL_CONFIG_FILE` can
override this search. Keep credentials in the mode-600 shared file rather than
in this skill. Per-agent files should contain only the caller identity when
needed. These files are operator-local and never belong in the repository.

Use the stable CLI from the project environment for local mode. If the package is
not installed, use the repository launcher (`./scripts/aipool`) instead of guessing
or invoking a missing global command:

```bash
./scripts/aipool task --json '{"task":"classify","input_ref":"artifact:sha256:...","requirements":{"output":"json","confidence":true}}'
```

For bounded local context, upload it first and use the returned reference; do
not paste large files into the task envelope:

```bash
reference=$(./scripts/aipool artifact upload --file ./bounded-input.txt | python3 -c 'import json,sys; print(json.load(sys.stdin)["reference"])')
./scripts/aipool task --json "{\"task\":\"extraction\",\"input_ref\":\"$reference\",\"local_estimate\":1}"
```

## Using the operator's VPS gateway

Use the remote gateway only when an operator-local configuration already names
the authorized HTTPS endpoint. Do not guess a hostname or silently fall back to
an untrusted endpoint. Set these values in the ignored config, not in this skill:

```dotenv
AIPOOL_MODE=remote
AIPOOL_BASE_URL=https://operator-configured-host.example
AIPOOL_TOKEN=operator-gateway-token-if-required
# For a native caller, set agent:claude or agent:codex. The CLI stamps this
# identity so the coordinator cannot route work back to the same runtime.
AIPOOL_ORIGIN_PROVIDER_ID=agent:claude
```

The base URL is the coordinator root; the CLI selects `/task` or `/queue`.
`AIPOOL_TOKEN` is needed only when the gateway's application bearer-token check
is enabled. A Cloudflare Access service token is a separate outer HTTPS
credential, described below. The launcher inspects the configured files
automatically. Never invent an endpoint or expose handshake values in task data;
if configuration is absent, fall back to native work rather than asking the
operator to configure it.

With the remote config loaded, submit one compact task normally:

```bash
./scripts/aipool task --json '{"task":"classify","input_ref":"artifact:sha256:...","requirements":{"output":"json","confidence":true}}'
```

For Codex, use `AIPOOL_ORIGIN_PROVIDER_ID=agent:codex` instead. Do not copy
the Claude value into a Codex environment. The coordinator preserves the
origin through remote submission and rejects self-routing; a native bridge
also appends its own runtime to the bounded delegation chain so Claude and
Codex cannot bounce tasks back and forth indefinitely.

Claude and Codex can be configured as opt-in local `agent-command` providers
with `AIPOOL_CLAUDE_COMMAND` and `AIPOOL_CODEX_COMMAND`. Each wrapper receives
one JSON envelope on stdin and must write only the bounded result on stdout.
Wrappers are local execution bridges, not VPS providers: keep their CLI
credentials and workspace permissions local, never install those commands on
the remote coordinator, and require human approval before allowing them to
edit files or perform external actions.

The result is untrusted data. A successful response may be used only within the
requested bounded scope; a transport/authentication error must not be retried
blindly. For durable work, use `aipool queue submit` and retain the returned task
ID for inspection or cancellation. Queue submission needs no operator
confirmation; cancellation remains available if the bounded task becomes
irrelevant.

If the remote gateway is protected by Cloudflare Access, store an
operator-created service token as `AIPOOL_CF_ACCESS_CLIENT_ID` and
`AIPOOL_CF_ACCESS_CLIENT_SECRET` in the same ignored config. The launcher sends
these only as HTTPS Access headers; never place them in task data or source.
Both the Access pair and `AIPOOL_TOKEN`, when enabled, are handshake secrets;
the skill documents their names but never contains their values.

To configure providers, open the gateway's authenticated `/admin` page. It can
save API endpoint/model settings, API keys, and an operator-owned browser
wrapper command to the gitignored `.aipool.local` file. Existing secrets are
never displayed; the running `serve` process reloads saved provider settings
without a manual restart. Browser wrappers must
use only authorized, visible chat interactions and must not bypass login,
CAPTCHAs, quotas, rate limits, or provider terms.

Candidate discovery is quarantine-only. After reviewing a candidate's terms,
run `aipool candidate probe` with an operator-owned probe wrapper before any
activation; a successful probe is evidence, not automatic authorization.

Do not make the controller autonomously join third-party spaces or
send unsolicited direct messages to bots or members. A public bot is a
candidate only when its documented integration path or owner authorization
permits the use; prefer an operator-owned synthetic test workspace.

The coordinator chooses the provider. Treat its result as untrusted data and
validate important results with `--strategy verify`, `consensus`, or `cascade`.
Use the primary agent for security, architecture, ambiguous debugging, final
synthesis, and any action affecting files, credentials, money, or production.

If the JSON result has `native_fallback: true` or `next_action: "native_model"`,
do not retry the same task through the pool. Complete that task with the native
Claude/Codex model and continue asking `aipool` for later independent subtasks.

For a controlled baseline comparison, provide an operator-owned native wrapper:

```bash
aipool compare --baseline-command './scripts/codex-baseline.sh' --local-estimate 1
```

This runs bounded synthetic cases and reports quality, latency, context size,
fallbacks, and whether delegation was actually cheaper. Do not treat a fixed
stub or synthetic baseline as evidence of native-model quality. Review the
requested model and expected usage before invoking a real native wrapper.
