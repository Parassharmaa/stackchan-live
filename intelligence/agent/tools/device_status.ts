import { defineTool } from "eve/tools";
import { z } from "zod";

import { deviceRequest } from "../lib/core.js";

export default defineTool({
  description:
    "Read the connected Stack-chan hardware status, including audio mode, sensor readiness, boot count, and verified motion capability.",
  inputSchema: z.object({}),
  async execute(_input, ctx) {
    return deviceRequest(ctx.session.id, "");
  },
});
