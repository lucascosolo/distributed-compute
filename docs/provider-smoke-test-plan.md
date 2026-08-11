# Provider smoke-test plan

This is the operator checklist for the first real comparison of configured
providers. It is deliberately separate from startup: opening the dashboard and
checking readiness must not spend provider quota.

## What the test compares

The coordinator will choose representative configured model cards from the
provider families. It will use the existing bounded synthetic cases:

- classify a short input and return the required JSON;
- extract structured fields from a short input;
- summarize a short input within a small limit.

Results should compare validity, capability scores, latency, errors, rate-limit
responses, usage accounting, and quota-hold behavior. Persist metadata and
scores, not secrets or unnecessary prompt/output content.

## Safety boundary

The first batch must be bounded and synthetic. It must not use private project
context, create accounts, activate routing, or bypass a provider's limits. A
provider that returns a login page, CAPTCHA, refusal, or unusable output is a
failed candidate for this run, not something to work around.

The dashboard must show the selected families/models, number of calls, and
expected quota impact before the run. The operator approves that exact batch.
Approval starts testing only; quarantine remains in place until the results are
reviewed and activation is separately approved.

## Current operator state

Many catalog families have keys configured, including Ollama Cloud. The catalog
contains multiple model cards per family, but the dashboard now presents one
family card with model details collapsed by default. Unconfigured families are
shown first. No provider smoke-test results should be inferred from a saved key;
key presence only means the candidate can be attempted.

## Follow-up

Implement a bounded batch endpoint/UI that partitions the configured catalog,
accounts for provider-specific request/token windows, and reports a comparison
without starting calls automatically. Then request human approval for the first
batch and run it sequentially with a small concurrency limit. Keep weak or
uncertain models quarantined, and use native Claude/Codex fallback when the
orchestrator cannot justify delegation.
