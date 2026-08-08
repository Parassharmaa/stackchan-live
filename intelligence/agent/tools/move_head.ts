import { defineTool } from "eve/tools";
import { z } from "zod";

import { dispatchDeviceControl } from "../lib/core.js";

export default defineTool({
  description:
    "Request bounded physical head movement. Use only for an explicit movement request or a clearly appropriate embodied reaction. Dispatch is not completion.",
  inputSchema: z.object({
    yaw_deg: z.number().min(-35).max(35).optional(),
    pitch_deg: z.number().min(5).max(85).optional(),
    duration_ms: z.number().int().min(200).max(1500).default(500),
  }),
  async execute(input, ctx) {
    return dispatchDeviceControl(ctx.session.id, "motion.set", input);
  },
});
