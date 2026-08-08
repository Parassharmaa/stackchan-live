import { defineTool } from "eve/tools";
import { z } from "zod";

import { dispatchDeviceControl } from "../lib/core.js";

export default defineTool({
  description:
    "Request one coordinated safe face, head, light, and optional music routine. Use music only when explicitly requested. Dispatch is not completion.",
  inputSchema: z.object({
    name: z.enum([
      "greet",
      "celebrate",
      "curious",
      "comfort",
      "dance",
      "wake_up",
      "focus",
      "good_night",
    ]),
    intensity: z.number().min(0.2).max(1).default(0.7),
    music: z.boolean().default(false),
  }),
  async execute(input, ctx) {
    return dispatchDeviceControl(ctx.session.id, "routine.play", input);
  },
});
