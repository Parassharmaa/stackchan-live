import { defineTool } from "eve/tools";
import { z } from "zod";

import { deviceRequest } from "../lib/core.js";

export default defineTool({
  description: "Delete one exact local schedule after an explicit user request.",
  inputSchema: z.object({ schedule_id: z.number().int().positive() }),
  async execute({ schedule_id }, ctx) {
    return deviceRequest(ctx.session.id, `/schedules/${schedule_id}`, {
      method: "DELETE",
    });
  },
});
