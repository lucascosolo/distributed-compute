# Provider endpoint audit

This audit is a preflight record for smoke tests. It checks endpoint shape and
adapter compatibility from primary documentation where available. It does not
send model requests, validate credentials, or prove that a tier is free.

| Family | Endpoint / transport | Preflight result | Notes |
| --- | --- | --- | --- |
| Hugging Face Inference Providers | `router.huggingface.co/v1/chat/completions` / HF chat API | verified | Official docs show this exact endpoint. The account has already shown paid usage, so it is quarantined as non-free. |
| Google AI Studio | `generativelanguage.googleapis.com/v1beta/openai/` / OpenAI-compatible | verified | Official OpenAI-compatibility docs show this base path. |
| Cerebras | `api.cerebras.ai/v1` / OpenAI-compatible | verified | Official quickstart uses `/v1/chat/completions`; current public models and non-permanent-free status are recorded in the catalog. |
| Groq | `api.groq.com/openai/v1` / OpenAI-compatible | verified | Official API reference uses `/openai/v1/chat/completions`. |
| Cohere | `api.cohere.ai/compatibility/v1` / OpenAI-compatible | corrected | The catalog previously pointed at the native `/v1` API while claiming a generic adapter. Official docs show the compatibility path; model metadata was refreshed. |
| Cloudflare Workers AI | `api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}` / native REST | adapter implemented, configuration pending | The panel now accepts the non-secret account ID beside the API token. The native adapter handles the account-scoped path, `result.response` envelope, authentication errors, and retry-after rate limits; it remains unloaded until both values are present. The catalog was refreshed away from deprecated Llama/Qwen IDs to `@cf/zai-org/glm-4.7-flash` and `@cf/qwen/qwen3-30b-a3b-fp8`. |
| OpenRouter | `openrouter.ai/api/v1` / OpenAI-compatible | verified | Official docs show `/api/v1/chat/completions`. |
| Z.AI GLM | `api.z.ai/api/paas/v4` / OpenAI-compatible | endpoint verified, model access pending | Z.AI’s official SDK documentation confirms the overseas base path. Model IDs and account-region access still need confirmation before testing. |
| TokenRouter | `api.tokenrouter.com/v1` / OpenAI-compatible | base path provisionally verified, model access pending | The provider’s official site/console references this base URL; the separate `.io` documentation uses a different host, so model IDs and the applicable documentation must be reconciled before testing. |
| NVIDIA NIM hosted API | `integrate.api.nvidia.com/v1` / OpenAI-compatible | shape verified | NVIDIA’s NIM API is OpenAI-compatible; hosted build.nvidia.com model IDs still require live discovery before testing. |
| Mistral AI | `api.mistral.ai/v1` / OpenAI-compatible | verified | Official docs use `/v1/chat/completions`. |
| SambaNova Cloud | `api.sambanova.ai/v1` / OpenAI-compatible | verified | Official API-key docs show `/v1/chat/completions`; current free availability remains account-dependent. |
| Aion Labs | `api.aionlabs.ai/v1` / OpenAI-compatible | verified | Official documentation confirms OpenAI-compatible chat completions and a `/v1/models` discovery endpoint. |
| Kilo Gateway | `api.kilo.ai/api/gateway` / OpenAI-compatible | operator-supplied, primary-doc check pending | Keep quarantined until the gateway’s own API documentation confirms the path. |
| Ollama Cloud | `ollama.com/v1` / OpenAI-compatible | verified | Official Ollama compatibility material documents this cloud base URL. |
| BazaarLink | `bazaarlink.ai/api/v1` / OpenAI-compatible | verified, model discovery pending | Official documentation confirms the OpenAI-compatible base path and says full provider/model IDs are required; catalog IDs still need a no-generation `/models` check. |
| xAI | `api.x.ai/v1` / OpenAI-compatible | verified | Official xAI docs use `/v1/chat/completions`. |

## Test gate

Do not run another multi-provider batch while any selected model is marked
“model access pending,” “primary-doc check pending,” or “not loaded.” A future
batch plan should show the endpoint audit status, selected model IDs, expected
calls, and quota headroom before the human approves it. Endpoint verification
does not replace a one-call credential and response-shape check; it prevents
wasting a full three-case benchmark on an obvious integration mismatch.

## Next implementation chunk

Cloudflare-specific configuration fields and its native adapter are now
implemented. The next step is deployment and a no-generation readiness check;
only after the operator confirms the account ID and reviews the remaining
provider states should any live smoke call be considered.
