# OmniRoute integration boundary

OmniRoute is being evaluated as an optional self-hosted upstream gateway, not as
a replacement for aipool's capability, quota, cost, health, and human-approval
logic. The coordinator remains the authority that decides whether a task is
delegated. OmniRoute may provide additional model routes behind one local
OpenAI-compatible endpoint.

## Findings from the current official documentation

- The documented API is OpenAI-compatible at `/v1/*`; the default single-port
  server uses port `20128`.
- The current release documents a base Docker profile and a bundled Redis
  sidecar. Its container runtime defaults to a 1 GiB Node heap, so the VPS
  memory impact must be measured before enabling the service alongside aipool.
- The current release does **not** reliably accept every provider key through
  environment variables. Its environment reference says most direct provider
  keys were removed from static env handling and should be entered through the
  dashboard, `data/provider-credentials.json`, or its encrypted database.
- The gateway supports API-key protection, scoped remote tokens, routing
  decision headers, quota-aware routing, and a dashboard. These must remain
  enabled or independently verified before it handles aipool traffic.
- The provider reference currently lists the API-key IDs that correspond to
  several of our families (`aion`, `cerebras`, `cloudflare-ai`, `cohere`,
  `gemini`, `groq`, `huggingface`, `mistral`, `nvidia`, `ollama-cloud`,
  `openrouter`, `xai`, and `zai`). Its catalog is evidence to review, not proof
  that every listed free-tier claim is current; notably, stale or retired
  entries must not be imported automatically.

## Integration shape

1. Run OmniRoute loopback-only on the VPS first. Do not publish port `20128`
   directly or expose its dashboard through Cloudflare until authentication and
   an explicit access policy are configured.
2. Give OmniRoute its own persistent data directory and encryption/auth secrets.
   Never mount aipool's SQLite database or copy its config file into OmniRoute.
3. Import only operator-selected keys through the documented credential path.
   The import must be an explicit, logged action with a mapping preview; it must
   not silently copy every saved key. Hugging Face is excluded by default because
   this account has already demonstrated paid usage.
4. Add OmniRoute as an optional aggregate transport in aipool. Discover its
   live model list, represent each selected model as a separate catalog profile,
   and keep paid fallback and unknown routes disabled.
5. Preserve the outer coordinator's cost and quota accounting. OmniRoute's
   `auto` route is not sufficient evidence for capability or free usage; routing
   decisions and provider usage headers must be recorded before activation.
6. Run authenticated no-generation `/v1/models` and health checks first. Only
   after a human reviews the model list, key mapping, quota behavior, and
   rollback path may a bounded generation smoke test be approved.

The checked-in `deploy/omniroute.compose.yml` is the first-stage runtime shape.
It uses separate bind-mounted data directories, a private Redis sidecar, API-key
protection, and a 512 MiB Node heap cap. It intentionally contains no provider
credentials.

## Human checkpoint before key import

The import will copy selected secrets from the existing aipool operator config
into OmniRoute's encrypted provider store on the VPS. This changes where those
credentials are held and allows OmniRoute to send requests to their providers.
It may consume the providers' quotas and may expose prompts to any selected
upstream. The reversible action is to disable the OmniRoute service and delete
or rotate the imported credentials through its dashboard; the aipool keys remain
unchanged unless separately rotated.

No key import or external provider generation call is authorized by this design
document alone.
