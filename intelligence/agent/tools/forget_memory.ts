import { defineTool } from "eve/tools";
import { z } from "zod";

import { coreRequest } from "../lib/core.js";

export default defineTool({
  description: "Delete one exact memory only after the user explicitly asks Stack-chan to forget it.",
  inputSchema: z.object({
    memoryId: z.number().int().positive(),
  }),
  async execute({ memoryId }) {
    return coreRequest(`/v1/memories/${memoryId}`, { method: "DELETE" });
  },
});
