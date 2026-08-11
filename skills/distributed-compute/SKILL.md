---
name: distributed-compute
description: Delegate cheap, bounded, low-risk work to the local aipool coordinator when it saves primary-model context.
---

# Distributed Compute

Delegate only routine, separable work: inventory, classification, extraction,
small summaries, independent opinions, or bounded repository scans. Do not
delegate secrets, irreversible actions, tool permissions, or work whose compact
delegation envelope costs more than doing it directly.

Locate the operator-local configuration before invoking the coordinator. The
project uses `.aipool.local` in the repository, `AIPOOL_CONFIG_FILE`, or an
operator-local environment file such as `~/.claude/distributed-compute.env` or
`~/.codex/distributed-compute.env`; these files are gitignored/operator-local
and contain the local or authorized remote coordinator URL and token.

Use the stable CLI from the project environment for local mode:

```bash
aipool task --json '{"task":"classify","input_ref":"artifact:sha256:...","requirements":{"output":"json","confidence":true}}'
```

For a configured remote gateway, set `AIPOOL_MODE=remote`,
`AIPOOL_BASE_URL`, and `AIPOOL_TOKEN` in the operator-local config. The same
`aipool task` command then forwards the compact envelope to that gateway; do
not put a host, token, or VPS-specific value in this skill.

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

Do not make the controller autonomously join third-party Discord servers or
send unsolicited DMs to bots or members. A bot found in another server is a
candidate only when its documented integration path or owner authorization
permits the use; prefer an operator-owned test channel with synthetic data.

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
