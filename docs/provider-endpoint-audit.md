# Provider endpoint audit

This audit is a preflight record for smoke tests. It checks endpoint shape and
adapter compatibility from primary documentation where available. It does not
send model requests, validate credentials, or prove that a tier is free.
The panel's model discovery action is a separate, human-initiated no-generation
GET request and remains subject to provider terms and rate limits.

| Family | Endpoint / transport | Preflight result | Notes |
| --- | --- | --- | --- |
| Hugging Face Inference Providers | `router.huggingface.co/v1/chat/completions` / HF chat API | verified | Official docs show this exact endpoint. The account has already shown paid usage, so it is quarantined as non-free. |
| Google AI Studio | `generativelanguage.googleapis.com/v1beta/openai/` / OpenAI-compatible | verified | Official OpenAI-compatibility docs show this base path. |
| Cerebras | `api.cerebras.ai/v1` / OpenAI-compatible | verified | Official quickstart uses `/v1/chat/completions`; current public models and non-permanent-free status are recorded in the catalog. |
| Groq | `api.groq.com/openai/v1` / OpenAI-compatible | verified, catalog refreshed | Official API reference uses `/openai/v1/chat/completions`; the catalog now avoids IDs scheduled for deprecation and uses current GPT-OSS/Qwen entries. |
| Cohere | `api.cohere.ai/compatibility/v1` / OpenAI-compatible | corrected, quota clarified | The catalog previously pointed at the native `/v1` API while claiming a generic adapter. Official docs show the compatibility path; Command A+ is documented as free until rate limits, while Command A publishes paid prices, so the family remains account-dependent until verified. |
| Cloudflare Workers AI | `api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}` / native REST | adapter implemented, configuration pending | The panel now accepts the non-secret account ID beside the API token. The native adapter handles the account-scoped path, `result.response` envelope, authentication errors, and retry-after rate limits; it remains unloaded until both values are present. The catalog was refreshed away from deprecated Llama/Qwen IDs to `@cf/zai-org/glm-4.7-flash` and `@cf/qwen/qwen3-30b-a3b-fp8`. |
| OpenRouter | `openrouter.ai/api/v1` / OpenAI-compatible | verified | Official docs show `/api/v1/chat/completions`. |
| Z.AI GLM | `api.z.ai/api/paas/v4` / OpenAI-compatible | endpoint and catalog IDs verified, account access pending | Z.AI’s official documentation confirms the overseas base path and `glm-4.7`/`glm-4.7-flash` IDs. Account-region access and quota status still need confirmation before testing. |
| TokenRouter | `api.tokenrouter.com/v1/responses` / Responses API | adapter implemented, host contract pending | The adapter now uses the documented `/responses` request and response shape rather than chat completions. The operator supplied the `.com` host, while detailed public docs currently use `.io`; do not send traffic until the `.com` host is confirmed. |
| NVIDIA NIM hosted API | `integrate.api.nvidia.com/v1` / OpenAI-compatible | shape verified, catalog refreshed | NVIDIA’s hosted endpoint is OpenAI-compatible. The catalog now uses current free-endpoint IDs shown in NVIDIA’s model directory; a no-generation `/models` check must still confirm the operator key’s access. |
| Mistral AI | `api.mistral.ai/v1` / OpenAI-compatible | verified, catalog refreshed | Official docs use `/v1/chat/completions`; the current model pages list `mistral-small-2603`, `codestral-2508`, and `mistral-large-2512`. Their published prices mean the operator must confirm free eligibility before any call. |
| SambaNova Cloud | `api.sambanova.ai/v1` / OpenAI-compatible | verified, quota metadata refreshed | Official API docs confirm `/v1/chat/completions` and `/v1/models`; the documented free tier is 20 RPM, 20 RPD, and 200,000 TPD for the current selected models when no payment method is linked. The operator currently has this family disabled. |
| Aion Labs | `api.aionlabs.ai/v1` / OpenAI-compatible | verified | Official API reference confirms OpenAI-compatible chat completions and a no-auth `/v1/models` discovery endpoint; the catalog IDs match the current model table. |
| Kilo Gateway | `api.kilo.ai/api/gateway` / OpenAI-compatible | verified, model availability pending | Official Kilo docs confirm `/chat/completions`, `/models`, Bearer authentication, and anonymous free-model access. The catalog now uses the documented `minimax/minimax-m2.1:free` and `z-ai/glm-5:free` IDs; the adapter can omit authentication for these selected free models, but a no-generation `/models` check must confirm they remain available. |
| Ollama Cloud | `ollama.com/v1` / OpenAI-compatible | verified | Official Ollama compatibility material documents this cloud base URL. |
| BazaarLink | `bazaarlink.ai/api/v1` / OpenAI-compatible | verified, model discovery pending | Official documentation confirms the OpenAI-compatible base path, `auto:free`, and current free limits. The catalog is restricted to documented free routes; a no-generation `/models` check must confirm current availability. |
| xAI | `api.x.ai/v1` / OpenAI-compatible | verified | Official xAI docs use `/v1/chat/completions`. |

## Test gate

Do not run another multi-provider batch while any selected model is marked
“model access pending,” “primary-doc check pending,” or “not loaded.” A future
batch plan should show the endpoint audit status, selected model IDs, expected
calls, and quota headroom before the human approves it. Endpoint verification
does not replace a one-call credential and response-shape check; it prevents
wasting a full three-case benchmark on an obvious integration mismatch.

## Next implementation chunk

Finish the remaining provider-contract audit, starting with TokenRouter’s
conflicting `.com` versus `.io` documentation and Kilo’s live model catalog.
Then add any required provider-specific adapters or quarantine entries. Keep
Cloudflare unloaded until the operator saves its account ID, and do not run a
live smoke call while any provider contract remains unresolved.
