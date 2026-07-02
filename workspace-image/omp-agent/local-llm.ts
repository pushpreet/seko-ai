// local-llm — registers the homelab LiteLLM/vLLM endpoint as an omp (oh-my-pi) provider,
// fully from env. This mirrors the pi harness extension (pi-agent/local-llm.ts) so both
// harnesses reach the same endpoint identically; only the import path and config shape
// differ (omp is a fork of pi).
//
// Why an extension instead of ~/.omp/agent/models.yml?
//   models.yml does NOT interpolate env vars in `baseUrl` (only in apiKey/headers), and
//   this image must be portable across endpoints (localhost on the GPU box, a dev VM, or a
//   friend's machine hitting https://llm.pushprh.com/v1). A TypeScript extension can read
//   process.env freely and register the provider dynamically, including a dynamic baseUrl.
//
// Env (all optional except in practice LLM_API_KEY for the public endpoint):
//   LLM_BASE_URL   OpenAI-compatible base, default http://host.docker.internal:8000/v1
//   LLM_API_KEY    ****** (LiteLLM virtual key / issued key). Empty = unauth server.
//   LLM_MODEL      Served-model id (default qwen3.6-27b). Registered as local/<id>.
//   LLM_MODEL_NAME Friendly label shown in the model picker (default = the id).
//   LLM_CONTEXT_WINDOW  Context tokens (default 262144 — Qwen3.6-27B @ 262K).
//   LLM_MAX_TOKENS      Max output tokens (default 16384).
//
// The provider is registered as "local"; config.yml pins modelRoles.default to
// local/<LLM_MODEL> so omp launches straight into it.
//
// `import type` is erased at transpile time, so the @oh-my-pi/pi-coding-agent package does
// not need to be resolvable at runtime (the omp binary bundles its own runtime).
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const BASE_URL = process.env.LLM_BASE_URL?.trim() || "http://host.docker.internal:8000/v1";
const MODEL = process.env.LLM_MODEL?.trim() || "qwen3.6-27b";
const MODEL_NAME = process.env.LLM_MODEL_NAME?.trim() || "";
const CONTEXT_WINDOW = Number(process.env.LLM_CONTEXT_WINDOW) || 262144;
const MAX_TOKENS = Number(process.env.LLM_MAX_TOKENS) || 16384;

export default function (pi: ExtensionAPI): void {
  // Register synchronously so the provider is guaranteed present before model selection,
  // regardless of how the loader awaits the default export. seko-ai always injects
  // LLM_MODEL, so no /models discovery is needed here (unlike friends running pi bare).
  pi.registerProvider("local", {
    name: "Local (homelab vLLM)",
    baseUrl: BASE_URL,
    apiKey: process.env.LLM_API_KEY ?? "", // empty is fine for an unauthenticated server
    authHeader: true, // send Authorization: ******
    api: "openai-completions",
    models: [
      {
        id: MODEL,
        name: MODEL_NAME || MODEL,
        // Qwen3.6 is a reasoning model; vLLM is configured with a reasoning parser.
        reasoning: true,
        input: ["text", "image"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, // local = free
        contextWindow: CONTEXT_WINDOW,
        maxTokens: MAX_TOKENS,
      },
    ],
  });
}
