const coreUrl = (process.env.STACKCHAN_CORE_URL ?? "http://127.0.0.1:8765").replace(
  /\/$/,
  "",
);

export async function coreRequest(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${coreUrl}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...init?.headers,
    },
    signal: AbortSignal.timeout(5_000),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Stack-chan core returned ${response.status}: ${detail.slice(0, 300)}`);
  }
  return response.json();
}

export async function deviceRequest(
  eveSessionId: string,
  path: string,
  init?: RequestInit,
): Promise<unknown> {
  return coreRequest(
    `/v1/eve-sessions/${encodeURIComponent(eveSessionId)}/device${path}`,
    init,
  );
}

export async function dispatchDeviceControl(
  eveSessionId: string,
  type: "face.set" | "lights.set" | "motion.set" | "routine.play",
  payload: Record<string, unknown>,
): Promise<unknown> {
  return deviceRequest(eveSessionId, "/control", {
    method: "POST",
    body: JSON.stringify({ type, payload }),
  });
}
