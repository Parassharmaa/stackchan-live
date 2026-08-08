import { defineTool } from "eve/tools";
import { z } from "zod";

import { deviceRequest } from "../lib/core.js";

export default defineTool({
  description: "Pause or resume one exact local schedule after an explicit user request.",
  inputSchema: z.object({
    schedule_id: z.number().int().positive(),
    enabled: z.boolean(),
  }),
  async execute({ schedule_id, enabled }, ctx) {
    return deviceRequest(ctx.session.id, `/schedules/${schedule_id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    });
  },
});
