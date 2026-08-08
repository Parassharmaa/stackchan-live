import type { LanguageModelV4CallOptions } from "@ai-sdk/provider";
import type { LanguageModelMiddleware } from "ai";

import { selectAuthorizedToolForTranscript } from "./tool-policy.ts";

const CONTEXT_BOUNDARY = "</stackchan_turn_context_json>";
const PASSIVE_AUTOMATIC_TOOLS = new Set(["load_skill"]);

function finalUserTranscript(params: LanguageModelV4CallOptions): string | undefined {
  const finalMessage = params.prompt.at(-1);
  if (finalMessage?.role !== "user") {
    return undefined;
  }
  const text = finalMessage.content
    .filter((part): part is Extract<typeof part, { type: "text" }> => part.type === "text")
    .map((part) => part.text)
    .join("");
  const boundary = text.lastIndexOf(CONTEXT_BOUNDARY);
  if (boundary < 0) {
    return undefined;
  }
  return text.slice(boundary + CONTEXT_BOUNDARY.length).trim() || undefined;
}

function functionToolName(tool: NonNullable<LanguageModelV4CallOptions["tools"]>[number]) {
  return tool.type === "function" ? tool.name : undefined;
}

function authorizedToolName(params: LanguageModelV4CallOptions) {
  const transcript = finalUserTranscript(params);
  if (transcript === undefined) {
    return undefined;
  }
  const available = new Set((params.tools ?? []).flatMap((tool) => functionToolName(tool) ?? []));
  return selectAuthorizedToolForTranscript(transcript, available);
}

/**
 * Keep mutating authored tools fail-closed. The model receives exactly one
 * such tool only for an explicit command-shaped request, and Eve still owns
 * schema validation, execution, and the grounded follow-up response.
 */
export function authorizedToolMiddleware(): LanguageModelMiddleware {
  return {
    specificationVersion: "v4",
    async transformParams({ params }) {
      const selection = authorizedToolName(params);
      const finalRole = params.prompt.at(-1)?.role;
      if (finalRole !== "user") {
        return { ...params, tools: [], toolChoice: { type: "none" } };
      }
      if (selection === undefined) {
        const tools = (params.tools ?? []).filter((tool) => {
          const name = functionToolName(tool);
          return name !== undefined && PASSIVE_AUTOMATIC_TOOLS.has(name);
        });
        return {
          ...params,
          tools,
          toolChoice: tools.length > 0 ? { type: "auto" } : { type: "none" },
        };
      }
      return {
        ...params,
        tools: (params.tools ?? []).filter(
          (tool) => functionToolName(tool) === selection,
        ),
        toolChoice: { type: "tool", toolName: selection },
      };
    },
  };
}
