import { defineTool } from "eve/tools";
import { z } from "zod";

import { deviceRequest } from "../lib/core.js";

export default defineTool({
  description:
    "Create one durable local Stack-chan check-in only after the user explicitly gives its time or daily cadence, IANA timezone, quiet hours, language, and whether each occurrence may capture one photo. Never infer recurring camera consent.",
  inputSchema: z.object({
    label: z.string().min(1).max(80),
    prompt: z.string().min(1).max(500),
    language: z.enum(["en", "ja"]),
    routine: z.enum([
      "greet",
      "celebrate",
      "curious",
      "comfort",
      "dance",
      "wake_up",
      "focus",
      "good_night",
    ]),
    music: z.boolean().default(false),
    capture_photo: z.boolean().describe(
      "True only when the user explicitly authorizes one visible still on every occurrence.",
    ),
    recurrence: z.enum(["once", "daily"]),
    timezone: z.string().min(1).max(80).describe("IANA timezone such as Asia/Tokyo."),
    local_time: z.string().min(5).max(16).describe(
      "HH:MM for daily schedules or YYYY-MM-DDTHH:MM for a one-shot local time.",
    ),
    quiet_start: z.string().regex(/^(?:[01]\d|2[0-3]):[0-5]\d$/),
    quiet_end: z.string().regex(/^(?:[01]\d|2[0-3]):[0-5]\d$/),
  }),
  async execute(input, ctx) {
    return deviceRequest(ctx.session.id, "/schedules", {
      method: "POST",
      body: JSON.stringify(input),
    });
  },
});
