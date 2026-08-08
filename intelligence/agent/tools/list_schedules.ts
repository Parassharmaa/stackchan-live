import { defineTool } from "eve/tools";
import { z } from "zod";

import { deviceRequest } from "../lib/core.js";

export default defineTool({
  description:
    "List this Stack-chan's local schedules, including paused and completed one-shot schedules.",
  inputSchema: z.object({}),
  async execute(_input, ctx) {
    const result = await deviceRequest(ctx.session.id, "/schedules");
    const { schedules } = z
      .object({
        schedules: z.array(
          z.object({
            id: z.number().int().positive(),
            label: z.string(),
            recurrence: z.enum(["once", "daily"]),
            local_time: z.string(),
            timezone: z.string(),
            enabled: z.boolean(),
            last_status: z.string().nullable(),
          }),
        ),
      })
      .parse(result);
    return { count: schedules.length, schedules };
  },
});
