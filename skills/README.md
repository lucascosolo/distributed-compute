# Repository skills

Skills shipped with this project live under `skills/` so the repository is
self-contained and other users can import them into Claude, Codex, or another
agent runtime.

For Claude Code, install the skill into Claude's normal per-user skill tree:

```bash
mkdir -p ~/.claude/skills/distributed-compute
cp skills/distributed-compute/SKILL.md ~/.claude/skills/distributed-compute/SKILL.md
```

The skill intentionally contains no hostnames, tokens, database paths, or other
deployment-specific values. Configure those in an ignored `.aipool.local`, in
`AIPOOL_CONFIG_FILE`, or in an operator-local environment file such as
`~/.claude/distributed-compute.env` or `~/.codex/distributed-compute.env`.
