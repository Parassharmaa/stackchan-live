import { defineTool } from "eve/tools";
import { z } from "zod";

import { dispatchDeviceControl } from "../lib/core.js";

export default defineTool({
  description:
    "Request Stack-chan's body lights within safe brightness limits. A dispatched result is not physical completion.",
  inputSchema: z.object({
    red: z.number().int().min(0).max(255),
    green: z.number().int().min(0).max(255),
    blue: z.number().int().min(0).max(255),
    brightness: z.number().min(0).max(0.35).default(0.25),
    animation: z.enum(["solid", "pulse", "rainbow", "chase", "twinkle"]).default("solid"),
  }),
  async execute(input, ctx) {
    return dispatchDeviceControl(ctx.session.id, "lights.set", input);
  },
});
