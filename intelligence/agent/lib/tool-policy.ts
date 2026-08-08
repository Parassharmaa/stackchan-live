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
  {
    name: "capture_photo",
    matches: (transcript) =>
      explicitToolCommand(transcript, "capture_photo") ||
      /\b(?:take|capture|snap|shoot)\b.{0,24}\b(?:a |my |our )?(?:photo|picture|snapshot|selfie)\b/i.test(
        transcript,
      ) ||
      /\b(?:look at|look over|check|inspect)\s+(?:this|that|it|me)\b/i.test(
        transcript,
      ) ||
      /\bwhat(?:'s| is)\s+(?:this|that)\b/i.test(transcript) ||
      /\bwhat\s+(?:am i|i am)\s+(?:holding|showing)\b/i.test(transcript) ||
      /(?:写真を?撮って|写真を?撮影して|撮影して|自撮りして|(?:これ|それ|あれ)を?(?:見て|確認して|チェックして)|(?:これ|それ|あれ)(?:は)?(?:何|なに|どう見える))/.test(
        transcript,
      ),
  },
  {
    name: "create_schedule",
    matches: (transcript) =>
      explicitToolCommand(transcript, "create_schedule") ||
      completeNaturalScheduleCommand(transcript),
  },
  {
    name: "list_schedules",
    matches: (transcript) =>
      explicitToolCommand(transcript, "list_schedules") ||
      /^(?:please\s+)?(?:list|show|tell me|what are)\b.*\b(?:schedule|schedules|reminders|check-ins)\b/i.test(
        transcript,
      ) ||
      /(?:予定|スケジュール|リマインダー|チェックイン).*(?:一覧|見せて|教えて|何)/.test(
        transcript,
      ),
  },
  {
    name: "set_schedule_enabled",
    matches: (transcript) =>
      explicitToolCommand(transcript, "set_schedule_enabled") ||
      /^(?:please\s+)?(?:pause|resume|enable|disable)\s+(?:schedule\s+)?(?:id\s*)?#?\d+[.!?\s]*$/i.test(
        transcript,
      ) ||
      /(?:予定|スケジュール)(?:\s*ID)?\s*#?\d+\s*を?(?:一時停止|再開|有効|無効)(?:して|してください)?[。！？\s]*$/.test(
        transcript,
      ),
  },
  {
    name: "delete_schedule",
    matches: (transcript) =>
      explicitToolCommand(transcript, "delete_schedule") ||
      /^(?:please\s+)?(?:delete|remove|cancel)\s+(?:schedule\s+)?(?:id\s*)?#?\d+[.!?\s]*$/i.test(
        transcript,
      ) ||
      /(?:予定|スケジュール)(?:\s*ID)?\s*#?\d+\s*を?(?:削除|取り消)(?:して|してください)?[。！？\s]*$/.test(
        transcript,
      ),
  },
];

function completeNaturalScheduleCommand(transcript: string): boolean {
  const englishCommand = /^(?:please\s+)?(?:schedule|set up|create)\b.*\b(?:reminder|check-in|schedule)\b/i.test(
    transcript,
  );
  const englishCadence = /\b(?:daily|every day|tomorrow|once|on \d{4}-\d{2}-\d{2})\b/i.test(
    transcript,
  );
  const englishTimezone = /\b(?:Asia\/Tokyo|Tokyo time|JST|UTC)\b/i.test(transcript);
  const englishQuietHours = /\bquiet hours?\b/i.test(transcript);
  const englishCameraChoice = /\b(?:with|without|no) (?:a |the )?(?:camera|photo|picture|still)\b/i.test(
    transcript,
  );
  if (
    englishCommand &&
    englishCadence &&
    englishTimezone &&
    englishQuietHours &&
    englishCameraChoice
  ) {
    return true;
  }
  return (
    /^(?:予定|スケジュール|リマインダー|チェックイン).*(?:作って|設定して|登録して)/.test(
      transcript,
    ) &&
    /(?:毎日|一回|明日|\d{4}年\d{1,2}月\d{1,2}日)/.test(transcript) &&
    /(?:Asia\/Tokyo|日本時間|JST|UTC)/i.test(transcript) &&
    /(?:静かな時間|通知しない時間|クワイエットアワー)/.test(transcript) &&
    /(?:カメラ|写真).*(?:使う|あり|なし|使わない|撮る|撮らない)/.test(transcript)
  );
}

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
  /\bonce\s+(?:i|we|you|this|that|the)\b/i,
  /\bwhen\s+(?:i|we|you)\b/i,
  /\b(?:example|hypothetical|not\s+(?:a\s+)?request)\b/i,
  /\b(?:is\s+the\s+phrase|means\s+something|shown\s+in\s+documentation|shown\s+in\s+(?:a\s+)?manual)\b/i,
  /(?:もし|場合|後(?:で|に)|もう一度|再び|例(?:えば)?|依頼では(?:ない|ありません)|命令では(?:ない|ありません))/,
];

export function selectAuthorizedToolForTranscript(
  transcript: string,
  available: ReadonlySet<string>,
): string | undefined {
  return selectAuthorizedToolsForTranscript(transcript, available)[0];
}

function explicitToolSequence(
  transcript: string,
  available: ReadonlySet<string>,
): string[] {
  if (!/^(?:please\s+)?(?:use|call|run|invoke)\b/i.test(transcript)) {
    return [];
  }
  return SAFE_FORCED_TOOL_RULES.flatMap((rule) => {
    if (!available.has(rule.name)) return [];
    const escaped = rule.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const match = new RegExp(`(?:^|[^\\w])${escaped}(?:[^\\w]|$)`, "i").exec(transcript);
    return match === null ? [] : [{ name: rule.name, index: match.index }];
  })
    .sort((left, right) => left.index - right.index)
    .map((item) => item.name);
}

export function selectAuthorizedToolsForTranscript(
  transcript: string,
  available: ReadonlySet<string>,
): string[] {
  const normalized = transcript.trim();
  if (NON_AUTHORIZING_CONTEXT.some((pattern) => pattern.test(normalized))) {
    return [];
  }
  const explicit = explicitToolSequence(normalized, available);
  if (explicit.length > 0) return explicit;
  return SAFE_FORCED_TOOL_RULES.filter(
    (rule) => available.has(rule.name) && rule.matches(normalized),
  ).map((rule) => rule.name);
}
