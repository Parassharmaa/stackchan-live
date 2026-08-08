import { defineTool } from "eve/tools";
import { z } from "zod";

import { coreRequest } from "../lib/core.js";

export default defineTool({
  description: "Search Stack-chan's durable memory for facts relevant to a specific question.",
  inputSchema: z.object({
    query: z.string().min(1).max(500),
    limit: z.number().int().min(1).max(10).default(6),
  }),
  async execute({ query, limit }) {
    const params = new URLSearchParams({ query, limit: String(limit) });
    return coreRequest(`/v1/memories?${params}`);
  },
});
