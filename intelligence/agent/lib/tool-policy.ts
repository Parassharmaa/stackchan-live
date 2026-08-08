const SAFE_FORCED_TOOL_RULES: readonly {
  readonly name: string;
  readonly matches: (transcript: string) => boolean;
}[] = [
  {
    name: "forget_memory",
    matches: (transcript) =>
      explicitToolCommand(transcript, "forget_memory") ||
      /^(?:please\s+)?(?:forget|delete)\s+(?:the\s+)?memory(?:\s+id)?\s+#?\d+[.!?\s]*$/i.test(
        transcript,
      ) ||
      /^(?:メモリ|記憶)(?:\s*ID)?\s*#?\d+\s*を?(?:削除して|削除|忘れて)(?:ください|下さい)?[。！？\s]*$/.test(
        transcript,
      ),
  },
  {
    name: "remember",
    matches: (transcript) => explicitToolCommand(transcript, "remember"),
  },
  {
    name: "recall_memory",
    matches: (transcript) =>
      explicitToolCommand(transcript, "recall_memory") ||
      /^(?:what|which)\s+(?:facts?\s+)?do\s+you\s+remember\b/i.test(transcript) ||
      /^(?:do|can)\s+you\s+remember\b/i.test(transcript) ||
      /(?:何を|なにを).*(?:覚えて|記憶して)(?:いる|います)/.test(transcript),
  },
  {
    name: "list_memories",
    matches: (transcript) => explicitToolCommand(transcript, "list_memories"),
  },
  {
    name: "device_status",
    matches: (transcript) =>
      explicitToolCommand(transcript, "device_status") ||
      /^(?:are|is)\s+(?:your\s+)?(?:(?:physical|head)\s+){0,2}(?:sensor|sensors|hardware|device).*(?:ready|working|connected|okay|ok)\b/i.test(
        transcript,
      ) ||
      /^(?:what(?:'s| is)|show me)\s+(?:your\s+)?(?:device|hardware|sensor)\s+status\b/i.test(
        transcript,
      ) ||
      /(?:センサー|ハードウェア|本体).*(?:準備|動いて|接続|状態)/.test(transcript),
  },
  {
    name: "move_head",
    matches: (transcript) => explicitToolCommand(transcript, "move_head"),
  },
  {
    name: "set_face",
    matches: (transcript) => explicitToolCommand(transcript, "set_face"),
  },
  {
    name: "set_lights",
    matches: (transcript) => explicitToolCommand(transcript, "set_lights"),
  },
  {
    name: "play_routine",
    matches: (transcript) => explicitToolCommand(transcript, "play_routine"),
  },
];

function explicitToolCommand(transcript: string, name: string): boolean {
  const command = new RegExp(
    `^(?:please\\s+)?(?:use|call|run|invoke)\\s+(?:the\\s+)?${name}` +
      `(?:\\s+tool)?(?:\\s+(?:now\\b|to\\b|with\\b|using\\b|for\\b))`,
    "i",
  );
  return command.test(transcript);
}

const NON_AUTHORIZING_CONTEXT = [
  /\b(?:only\s+)?if\b/i,
  /\b(?:only\s+)?after\b/i,
  /\bunless\b/i,
  /\bonce\b/i,
  /\bwhen\s+(?:i|we|you)\b/i,
  /\b(?:example|hypothetical|not\s+(?:a\s+)?request)\b/i,
  /(?:もし|場合|後(?:で|に)|もう一度|再び|例(?:えば)?|依頼では(?:ない|ありません)|命令では(?:ない|ありません))/,
];

export function selectAuthorizedToolForTranscript(
  transcript: string,
  available: ReadonlySet<string>,
): string | undefined {
  const normalized = transcript.trim();
  if (NON_AUTHORIZING_CONTEXT.some((pattern) => pattern.test(normalized))) {
    return undefined;
  }
  return SAFE_FORCED_TOOL_RULES.find(
    (rule) => available.has(rule.name) && rule.matches(normalized),
  )?.name;
}
