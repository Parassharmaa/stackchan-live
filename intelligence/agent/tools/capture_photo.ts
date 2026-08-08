import { defineTool } from "eve/tools";
import { z } from "zod";

import { dispatchDeviceControl } from "../lib/core.js";

export default defineTool({
  description:
    "Automatically point toward the user and capture exactly one privacy-visible still with Stack-chan's onboard camera after an explicit photo request, a direct visual request such as 'look at this', 'what is this?', or 'how am I looking?', or a context-confirmed handoff such as 'here it is'. Do not ask the user to repeat a magic phrase. For visual questions, use the returned terminal local-vision result before answering and never claim what is visible from dispatch alone.",
  inputSchema: z.object({
    quality: z.number().int().min(40).max(85).default(70),
  }),
  async execute(input, ctx) {
    const pose = await dispatchDeviceControl(ctx.session.id, "motion.set", {
      yaw_deg: 0,
      pitch_deg: 45,
      duration_ms: 550,
    });
    const terminal = (pose as { terminal_result?: { success?: unknown } }).terminal_result;
    if (terminal?.success !== true) {
      return { pose, capture: null, success: false };
    }
    const capture = await dispatchDeviceControl(ctx.session.id, "camera.capture", input);
    const captureTerminal = (capture as { terminal_result?: { success?: unknown } }).terminal_result;
    return { pose, capture, success: captureTerminal?.success === true };
  },
});
