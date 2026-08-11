import { defineTool } from "eve/tools";
import { z } from "zod";

import { dispatchDeviceControl } from "../lib/core.js";

export default defineTool({
  description:
    "Perform one verified, bounded head gesture. Use nod or double_nod for agreement, shake_no for disagreement, bow for thanks or greeting, and attentive for a subtle listening reaction. Prefer this semantic tool over composing raw head positions. Dispatch is not completion.",
  inputSchema: z.object({
    name: z.enum(["nod", "double_nod", "shake_no", "bow", "attentive"]),
    intensity: z.number().min(0.35).max(1).default(0.7),
  }),
  async execute(input, ctx) {
    return dispatchDeviceControl(ctx.session.id, "gesture.play", input);
  },
});
