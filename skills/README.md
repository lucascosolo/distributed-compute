# Repository skills

Skills shipped with this project live under `skills/` so the repository is
self-contained and other users can import them into Claude, Codex, or another
agent runtime.

To install the distributed-compute skill into the shared local skill tree:

```bash
mkdir -p ~/.agents/skills/distributed-compute
cp skills/distributed-compute/SKILL.md ~/.agents/skills/distributed-compute/SKILL.md
```

The skill intentionally contains no hostnames, tokens, database paths, or other
deployment-specific values. Configure those in an ignored `.aipool.local`, in
`AIPOOL_CONFIG_FILE`, or in `~/.agents/distributed-compute.env`.
