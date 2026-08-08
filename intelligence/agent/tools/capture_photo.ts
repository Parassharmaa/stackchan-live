import { defineTool } from "eve/tools";
import { z } from "zod";

import { dispatchDeviceControl } from "../lib/core.js";

export default defineTool({
  description:
    "Capture one privacy-visible still with Stack-chan's onboard camera after an explicit photo request or a direct request to look at the user, such as 'How am I looking?'. For visual questions, capture first and use the returned local-vision result before answering. Never claim what is visible from a dispatched-only result.",
  inputSchema: z.object({
    quality: z.number().int().min(40).max(85).default(70),
  }),
  async execute(input, ctx) {
    return dispatchDeviceControl(ctx.session.id, "camera.capture", input);
  },
});
