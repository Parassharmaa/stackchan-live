import { defineAgent } from "eve";
import { experimental_chatgpt } from "eve/models/openai";
import { wrapLanguageModel } from "ai";

import { authorizedToolMiddleware } from "./lib/authorized-tool-middleware.js";

const model = process.env.STACKCHAN_EVE_MODEL?.trim() || "gpt-5.6-luna";
const subscriptionModel = experimental_chatgpt(model);
if (typeof subscriptionModel === "string") {
  throw new Error("experimental_chatgpt returned a model ID instead of a model instance");
}

export default defineAgent({
  description: "Bilingual durable intelligence for the physical Stack-chan companion.",
  model: wrapLanguageModel({
    model: subscriptionModel,
    middleware: authorizedToolMiddleware(),
  }),
  modelContextWindowTokens: 200_000,
  reasoning: "none",
  compaction: {
    thresholdPercent: 0.72,
  },
  limits: {
    sessionTimeoutMs: 7 * 24 * 60 * 60 * 1_000,
    maxInputTokensPerSession: 2_000_000,
    maxOutputTokensPerSession: 200_000,
  },
});
