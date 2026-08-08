import { defineTool } from "eve/tools";
import { z } from "zod";

import { deviceRequest } from "../lib/core.js";

export default defineTool({
  description:
    "List this Stack-chan's local schedules, including paused and completed one-shot schedules.",
  inputSchema: z.object({}),
  async execute(_input, ctx) {
    return deviceRequest(ctx.session.id, "/schedules");
  },
});
