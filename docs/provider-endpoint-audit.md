# Provider endpoint audit

This audit is a preflight record for smoke tests. It checks endpoint shape and
adapter compatibility from primary documentation where available. It does not
send model requests, validate credentials, or prove that a tier is free.
The panel's model discovery action is a separate, human-initiated no-generation
GET request and remains subject to provider terms and rate limits.
Catalog entries now carry a preflight status. Pending entries are shown with the
provider's review note, excluded from the batch plan, and rejected by both the
individual and batch smoke-test endpoints until their contract is verified.

| Family | Endpoint / transport | Preflight result | Notes |
| --- | --- | --- | --- |
| Hugging Face Inference Providers | `router.huggingface.co/v1/chat/completions` / HF chat API | verified | Official docs show this exact endpoint. The account has already shown paid usage, so it is quarantined as non-free. |
| Google AI Studio | `generativelanguage.googleapis.com/v1beta/openai/` / OpenAI-compatible | verified | Official OpenAI-compatibility docs show this base path. |
| Cerebras | `api.cerebras.ai/v1` / OpenAI-compatible | verified | Official quickstart uses `/v1/chat/completions`; current public models and non-permanent-free status are recorded in the catalog. |
| Groq | `api.groq.com/openai/v1` / OpenAI-compatible | verified, catalog refreshed | Official API reference uses `/openai/v1/chat/completions`; the catalog now avoids IDs scheduled for deprecation and uses current GPT-OSS/Qwen entries. |
| Cohere | `api.cohere.ai/compatibility/v1` / OpenAI-compatible | corrected, quota clarified | The catalog previously pointed at the native `/v1` API while claiming a generic adapter. Official docs show the compatibility path; Command A+ is documented as free until rate limits, while Command A publishes paid prices, so the family remains account-dependent until verified. |
| Cloudflare Workers AI | `api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}` / native REST | adapter implemented, configuration pending | The panel now accepts the non-secret account ID beside the API token. The native adapter handles the account-scoped path, `result.response` envelope, authentication errors, and retry-after rate limits; it remains unloaded until both values are present. The catalog was refreshed away from deprecated Llama/Qwen IDs to `@cf/zai-org/glm-4.7-flash` and `@cf/qwen/qwen3-30b-a3b-fp8`. |
| OpenRouter | `openrouter.ai/api/v1` / OpenAI-compatible | read-only access verified, catalog refreshed | Official docs show `/api/v1/chat/completions`; the saved key successfully returned `/models`, and the catalog now uses currently listed free IDs. Existing degraded health remains a separate generation-health issue. |
| Z.AI GLM | `api.z.ai/api/paas/v4` / OpenAI-compatible | read-only access verified, catalog refreshed | The saved key successfully returned `/models`; the current response lists `glm-5` and `glm-4.7`, so the stale `glm-4.7-flash` entry was removed. Generation remains quarantined. |
| TokenRouter | `api.tokenrouter.io/v1/responses` / Responses API | corrected, access pending | Official API docs specify the `.io` host, `/v1/responses`, Bearer authentication, and `auto:balance`. The catalog no longer uses the operator-supplied `.com` host; a no-generation credential/access check is still required before smoke testing. |
| NVIDIA NIM hosted API | `integrate.api.nvidia.com/v1` / OpenAI-compatible | read-only access verified | NVIDIA’s hosted endpoint is OpenAI-compatible. The saved key successfully returned `/models`, including the selected GPT-OSS route; generation remains quarantined pending operator approval. |
| Mistral AI | `api.mistral.ai/v1` / OpenAI-compatible | verified, catalog refreshed | Official docs use `/v1/chat/completions`; the current model pages list `mistral-small-2603`, `codestral-2508`, and `mistral-large-2512`. Their published prices mean the operator must confirm free eligibility before any call. |
| SambaNova Cloud | `api.sambanova.ai/v1` / OpenAI-compatible | verified, quota metadata refreshed | Official API docs confirm `/v1/chat/completions` and `/v1/models`; the documented free tier is 20 RPM, 20 RPD, and 200,000 TPD for the current selected models when no payment method is linked. The operator currently has this family disabled. |
| Aion Labs | `api.aionlabs.ai/v1` / OpenAI-compatible | verified | Official API reference confirms OpenAI-compatible chat completions and a no-auth `/v1/models` discovery endpoint; the catalog IDs match the current model table. |
| Kilo Gateway | `api.kilo.ai/api/gateway` / OpenAI-compatible | read-only access verified, catalog refreshed | Official Kilo docs confirm `/chat/completions`, `/models`, Bearer authentication, and anonymous free-model access. The current `/models` response exposed live free Nemotron routes, so the catalog was refreshed to `nvidia/nemotron-3-ultra-550b-a55b:free` and `nvidia/nemotron-3-super-120b-a12b:free`; generation remains quarantined. |
| Ollama Cloud | `ollama.com/v1` / OpenAI-compatible | verified | Official Ollama compatibility material documents this cloud base URL. |
| BazaarLink | `bazaarlink.ai/api/v1` / OpenAI-compatible | read-only access verified, catalog refreshed | Official documentation confirms the OpenAI-compatible base path, `auto:free`, and current free limits. The saved key successfully returned `/models`; the catalog now uses the observed `deepseek/deepseek-v4-flash:free` route and keeps paid fallback disabled. |
| xAI | `api.x.ai/v1` / OpenAI-compatible | access pending | Official xAI docs use `/v1/chat/completions`; the saved key returned HTTP 403 from the read-only `/models` check, so this family remains paused pending credential/account review. |

## Test gate

Do not run another multi-provider batch while any selected model is marked
“model access pending,” “primary-doc check pending,” or “not loaded.” A future
batch plan should show the endpoint audit status, selected model IDs, expected
calls, and quota headroom before the human approves it. Endpoint verification
does not replace a one-call credential and response-shape check; it prevents
wasting a full three-case benchmark on an obvious integration mismatch.

## Next implementation chunk

Finish the remaining provider-contract audit, starting with TokenRouter's
credential/access check and Kilo's live model catalog.
Then add any required provider-specific adapters or quarantine entries. Keep
Cloudflare unloaded until the operator saves its account ID, and do not run a
live smoke call while any provider contract remains unresolved.
