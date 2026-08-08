import { defineTool } from "eve/tools";
import { z } from "zod";

import { dispatchDeviceControl } from "../lib/core.js";

export default defineTool({
  description:
    "Request a safe semantic face expression on the physical Stack-chan. A dispatched result is not proof that the display completed it.",
  inputSchema: z.object({
    state: z.enum(["idle", "listening", "thinking", "speaking", "sleepy"]).default("idle"),
    emotion: z
      .enum([
        "neutral",
        "happy",
        "excited",
        "curious",
        "surprised",
        "sad",
        "crying",
        "sleepy",
        "love",
      ])
      .default("neutral"),
    intensity: z.number().min(0).max(1).default(0.7),
  }),
  async execute(input, ctx) {
    return dispatchDeviceControl(ctx.session.id, "face.set", input);
  },
});
