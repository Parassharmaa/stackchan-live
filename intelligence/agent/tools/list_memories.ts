import { defineTool } from "eve/tools";
import { z } from "zod";

import { coreRequest } from "../lib/core.js";

export default defineTool({
  description: "List recent durable memories when the user asks what Stack-chan remembers.",
  inputSchema: z.object({
    limit: z.number().int().min(1).max(20).default(10),
  }),
  async execute({ limit }) {
    return coreRequest(`/v1/memories?limit=${limit}`);
  },
});
