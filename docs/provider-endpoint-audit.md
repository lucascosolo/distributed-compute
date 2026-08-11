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
| Cloudflare Workers AI | `api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}` / native REST | verified, not loaded | The path requires an account ID and Cloudflare-specific request/response handling. It is not eligible for the generic OpenAI adapter yet. |
| OpenRouter | `openrouter.ai/api/v1` / OpenAI-compatible | verified | Official docs show `/api/v1/chat/completions`. |
| Z.AI GLM | `api.z.ai/api/paas/v4` / OpenAI-compatible | cataloged, primary-doc check pending | Keep quarantined until the official endpoint/model documentation is captured. |
| TokenRouter | `api.tokenrouter.com/v1` / OpenAI-compatible | operator-supplied, primary-doc check pending | Do not spend quota until the provider’s own API docs confirm the base path and model IDs. |
| NVIDIA NIM hosted API | `integrate.api.nvidia.com/v1` / OpenAI-compatible | shape verified | NVIDIA’s NIM API is OpenAI-compatible; hosted build.nvidia.com model IDs still require live discovery before testing. |
| Mistral AI | `api.mistral.ai/v1` / OpenAI-compatible | verified | Official docs use `/v1/chat/completions`. |
| SambaNova Cloud | `api.sambanova.ai/v1` / OpenAI-compatible | verified | Official API-key docs show `/v1/chat/completions`; current free availability remains account-dependent. |
| Aion Labs | `api.aionlabs.ai/v1` / OpenAI-compatible | operator-supplied, primary-doc check pending | Keep quarantined until official endpoint and model documentation are captured. |
| Kilo Gateway | `api.kilo.ai/api/gateway` / OpenAI-compatible | operator-supplied, primary-doc check pending | Keep quarantined until the gateway’s own API documentation confirms the path. |
| Ollama Cloud | `ollama.com/v1` / OpenAI-compatible | verified | Official Ollama compatibility material documents this cloud base URL. |
| BazaarLink | `bazaarlink.ai/api/v1` / OpenAI-compatible | operator-supplied, primary-doc check pending | Keep quarantined until the provider’s own API documentation confirms the path. |
| xAI | `api.x.ai/v1` / OpenAI-compatible | verified | Official xAI docs use `/v1/chat/completions`. |

## Test gate

Do not run another multi-provider batch while any selected model is marked
“primary-doc check pending.” A future batch plan should show the endpoint audit
status, selected model IDs, expected calls, and quota headroom before the human
approves it. Endpoint verification does not replace a one-call credential and
response-shape check; it prevents wasting a full three-case benchmark on an
obvious integration mismatch.
