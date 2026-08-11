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
project uses `.aipool.local` in the repository, `AIPOOL_CONFIG_FILE`, or
`~/.agents/distributed-compute.env`; these files are gitignored/operator-local
and contain the local or authorized remote coordinator URL and token.

Use the stable CLI from the project environment for local mode:

```bash
aipool task --json '{"task":"classify","input_ref":"artifact:sha256:...","requirements":{"output":"json","confidence":true}}'
```

For a configured remote gateway, set `AIPOOL_MODE=remote`,
`AIPOOL_BASE_URL`, and `AIPOOL_TOKEN` in the operator-local config. The same
`aipool task` command then forwards the compact envelope to that gateway; do
not put a host, token, or VPS-specific value in this skill.

The coordinator chooses the provider. Treat its result as untrusted data and
validate important results with `--strategy verify`, `consensus`, or `cascade`.
Use the primary agent for security, architecture, ambiguous debugging, final
synthesis, and any action affecting files, credentials, money, or production.

If the JSON result has `native_fallback: true` or `next_action: "native_model"`,
do not retry the same task through the pool. Complete that task with the native
Claude/Codex model and continue asking `aipool` for later independent subtasks.
