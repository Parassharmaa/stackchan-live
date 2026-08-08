import { defineTool } from "eve/tools";
import { z } from "zod";

import { coreRequest } from "../lib/core.js";

export default defineTool({
  description: "Store one non-sensitive fact only after the user explicitly asks Stack-chan to remember it.",
  inputSchema: z.object({
    content: z.string().min(1).max(500),
    language: z.enum(["en", "ja", "und"]),
  }),
  async execute({ content, language }) {
    return coreRequest("/v1/memories", {
      method: "POST",
      body: JSON.stringify({
        content,
        language,
        kind: "explicit",
        importance: 0.8,
      }),
    });
  },
});
