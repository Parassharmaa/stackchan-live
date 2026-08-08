import type { LanguageModelV4CallOptions } from "@ai-sdk/provider";
import type { LanguageModelMiddleware } from "ai";

import { selectAuthorizedToolsForTranscript } from "./tool-policy.ts";

const CONTEXT_OPEN = "<stackchan_turn_context_json>";
const CONTEXT_BOUNDARY = "</stackchan_turn_context_json>";
const PASSIVE_AUTOMATIC_TOOLS = new Set(["load_skill"]);
const MAX_OUTPUT_TOKENS_PER_STEP = 2_000;

type UserTurn = {
  readonly index: number;
  readonly transcript: string;
  readonly physicalActionResults: readonly string[];
};

function textContent(message: LanguageModelV4CallOptions["prompt"][number]): string {
  if (typeof message.content === "string") return message.content;
  return message.content
    .filter((part): part is Extract<typeof part, { type: "text" }> => part.type === "text")
    .map((part) => part.text)
    .join("");
}

function latestUserTurn(params: LanguageModelV4CallOptions): UserTurn | undefined {
  for (let index = params.prompt.length - 1; index >= 0; index -= 1) {
    const message = params.prompt[index];
    if (message?.role !== "user") continue;
    const text = textContent(message);
    const boundary = text.lastIndexOf(CONTEXT_BOUNDARY);
    if (boundary < 0) continue;
    const open = text.lastIndexOf(CONTEXT_OPEN, boundary);
    let physicalActionResults: string[] = [];
    if (open >= 0) {
      const rawContext = text.slice(open + CONTEXT_OPEN.length, boundary).trim();
      try {
        const context = JSON.parse(rawContext) as { physical_action_results?: unknown };
        if (Array.isArray(context.physical_action_results)) {
          physicalActionResults = context.physical_action_results.filter(
            (item): item is string => typeof item === "string",
          );
        }
      } catch {
        // The application context is untrusted data. Malformed context grants nothing.
      }
    }
    const transcript = text.slice(boundary + CONTEXT_BOUNDARY.length).trim();
    if (transcript) return { index, transcript, physicalActionResults };
  }
  return undefined;
}

function functionToolName(tool: NonNullable<LanguageModelV4CallOptions["tools"]>[number]) {
  return tool.type === "function" ? tool.name : undefined;
}

function toolNamesAfterUserTurn(
  params: LanguageModelV4CallOptions,
  userTurn: UserTurn,
): Set<string> {
  const names = new Set<string>();
  for (const message of params.prompt.slice(userTurn.index + 1)) {
    if (typeof message.content === "string") continue;
    for (const part of message.content) {
      if ((part.type === "tool-call" || part.type === "tool-result") && part.toolName) {
        names.add(part.toolName);
      }
    }
  }
  for (const result of userTurn.physicalActionResults) {
    const match = /^([a-z][a-z0-9_]*)\s+(?:physically completed|failed|was not physically confirmed)\b/i.exec(
      result,
    );
    if (match?.[1]) names.add(match[1]);
  }
  return names;
}

function authorizedToolNames(params: LanguageModelV4CallOptions): string[] {
  const userTurn = latestUserTurn(params);
  if (userTurn === undefined) return [];
  const available = new Set((params.tools ?? []).flatMap((tool) => functionToolName(tool) ?? []));
  const executed = toolNamesAfterUserTurn(params, userTurn);
  return selectAuthorizedToolsForTranscript(userTurn.transcript, available).filter(
    (name) => !executed.has(name),
  );
}

function boundedStep(params: LanguageModelV4CallOptions): LanguageModelV4CallOptions {
  return {
    ...params,
    maxOutputTokens: Math.min(params.maxOutputTokens ?? MAX_OUTPUT_TOKENS_PER_STEP, MAX_OUTPUT_TOKENS_PER_STEP),
  };
}

/**
 * Keep mutating authored tools fail-closed while allowing a turn to advance
 * through an ordered sequence. Each semantic model step receives exactly one
 * remaining authorized tool; completed tools are never re-exposed, and Eve
 * produces its grounded spoken reply after the sequence is exhausted.
 */
export function authorizedToolMiddleware(): LanguageModelMiddleware {
  return {
    specificationVersion: "v4",
    async transformParams({ params }) {
      const bounded = boundedStep(params);
      const selection = authorizedToolNames(bounded)[0];
      const finalRole = params.prompt.at(-1)?.role;
      if (selection === undefined) {
        const tools = finalRole === "user" ? (params.tools ?? []).filter((tool) => {
          const name = functionToolName(tool);
          return name !== undefined && PASSIVE_AUTOMATIC_TOOLS.has(name);
        }) : [];
        return {
          ...bounded,
          tools,
          toolChoice: tools.length > 0 ? { type: "auto" } : { type: "none" },
        };
      }
      return {
        ...bounded,
        tools: (params.tools ?? []).filter(
          (tool) => functionToolName(tool) === selection,
        ),
        toolChoice: { type: "tool", toolName: selection },
      };
    },
  };
}
