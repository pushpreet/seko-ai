// local-llm — registers the homelab vLLM endpoint as a pi provider, fully from env.
//
// Why an extension instead of ~/.pi/agent/models.json?
//   models.json only interpolates env vars in `apiKey`/`headers`, NOT in `baseUrl`.
//   This harness must be portable across endpoints (localhost on the GPU box, a dev VM,
//   or a friend's machine hitting https://llm.pushprh.com/v1) — so the base URL has to
//   come from the environment. A TypeScript extension can read process.env freely and
//   register the provider dynamically, which also lets us auto-discover the served model.
//
// Env (all optional except in practice LLM_API_KEY for the public endpoint):
//   LLM_BASE_URL   OpenAI-compatible base, default http://host.docker.internal:8000/v1
//   LLM_API_KEY    Bearer token (vLLM --api-key / your issued key). Empty = unauth server.
//   LLM_MODEL      Force a served-model id; otherwise auto-discovered from /models.
//   LLM_MODEL_NAME Friendly label shown in /model (default = the id).
//   LLM_CONTEXT_WINDOW  Context tokens (default 262144 — Qwen3.6-27B @ 262K).
//   LLM_MAX_TOKENS      Max output tokens (default 16384).
//
// Select it in pi with /model (provider "local"), or set settings.json "model".
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const BASE_URL = process.env.LLM_BASE_URL?.trim() || "http://host.docker.internal:8000/v1";
const FORCED_MODEL = process.env.LLM_MODEL?.trim() || "";
const MODEL_NAME = process.env.LLM_MODEL_NAME?.trim() || "";
const CONTEXT_WINDOW = Number(process.env.LLM_CONTEXT_WINDOW) || 262144;
const MAX_TOKENS = Number(process.env.LLM_MAX_TOKENS) || 16384;

// Fallback model id when discovery is unavailable (offline / unreachable endpoint).
const FALLBACK_MODEL = "qwen3.6-27b";

export default async function (pi: ExtensionAPI) {
  let modelIds: string[] = [];

  // Best-effort discovery of the served model(s) so friends don't need to know the id.
  // Guarded: a slow/unreachable endpoint must never block pi startup.
  if (!FORCED_MODEL) {
    try {
      const headers: Record<string, string> = {};
      const key = process.env.LLM_API_KEY?.trim();
      if (key) headers.Authorization = `Bearer ${key}`;
      const res = await fetch(`${BASE_URL.replace(/\/$/, "")}/models`, {
        headers,
        signal: AbortSignal.timeout(2500),
      });
      if (res.ok) {
        const payload = (await res.json()) as { data?: Array<{ id: string }> };
        modelIds = (payload.data ?? []).map((m) => m.id).filter(Boolean);
      }
    } catch {
      // ignore — fall through to the forced/fallback id below
    }
  }

  if (FORCED_MODEL) modelIds = [FORCED_MODEL];
  if (modelIds.length === 0) modelIds = [FALLBACK_MODEL];

  pi.registerProvider("local", {
    name: "Local (homelab vLLM)",
    baseUrl: BASE_URL,
    apiKey: "$LLM_API_KEY", // resolved from env at request time; empty is fine for unauth
    authHeader: true, // send Authorization: Bearer <LLM_API_KEY>
    api: "openai-completions",
    models: modelIds.map((id) => ({
      id,
      name: MODEL_NAME || id,
      // Qwen3.6 is a reasoning model; vLLM is configured with a reasoning parser so pi
      // can surface <think> separately. Tune thinking behaviour in Phase 2 against the
      // live server (see README "Tuning to mimic Copilot + Opus").
      reasoning: true,
      input: ["text", "image"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, // local = free
      contextWindow: CONTEXT_WINDOW,
      maxTokens: MAX_TOKENS,
      compat: {
        // vLLM's OpenAI server doesn't implement the "developer" role used for reasoning
        // models — send the system prompt as a normal "system" message instead.
        supportsDeveloperRole: false,
        // Qwen-style thinking toggle (chat_template_kwargs.enable_thinking).
        thinkingFormat: "qwen",
      },
    })),
  });
}
