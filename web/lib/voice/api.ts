/** FastAPI 면접 API 호출. 경로와 형식은 contracts의 계약을 따릅니다. */
import type { BrainReply, MetricEvent, SessionCreate, SessionCreated, TurnRequest, VoiceToken } from "@gitinsight/contracts";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`${status}: ${detail}`);
  }
}

async function postJson<T>(url: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  const data: unknown = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new ApiError(r.status, typeof detail === "string" ? detail : JSON.stringify(detail ?? data));
  }
  return data as T;
}

export function createSession(body: SessionCreate): Promise<SessionCreated> {
  return postJson("/api/sessions", body);
}

const TURN_TIMEOUT_MS = 8000;

/**
 * 답변을 보내고 다음 발화를 받습니다.
 * turn_id가 같으면 서버가 처음 결과를 그대로 돌려주므로, 시간 초과나 네트워크 오류 때 같은 turn_id로 한 번 더 보냅니다.
 */
export async function postTurn(
  sessionId: string,
  body: TurnRequest,
  opts: { labDelayMs?: number } = {},
): Promise<BrainReply> {
  const q = opts.labDelayMs ? `?lab_delay_ms=${opts.labDelayMs}` : "";
  const url = `/api/sessions/${sessionId}/turns${q}`;
  for (let attempt = 0; ; attempt++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), TURN_TIMEOUT_MS + (opts.labDelayMs ?? 0));
    try {
      return await postJson<BrainReply>(url, body, ctrl.signal);
    } catch (e) {
      const retryable = !(e instanceof ApiError) || e.status >= 500;
      if (attempt >= 1 || !retryable) throw e;
    } finally {
      clearTimeout(timer);
    }
  }
}

export function postMetric(ev: MetricEvent): void {
  // 측정 실패가 면접을 막으면 안 됩니다. 응답을 기다리지 않습니다.
  void fetch("/api/metrics", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(ev),
    keepalive: true,
  }).catch(() => undefined);
}

export function fetchVoiceToken(provider: VoiceToken["provider"]): Promise<VoiceToken> {
  // TODO: FastAPI의 POST /api/sessions/{id}/voice-token이 생기면 그쪽으로 바꿉니다.
  return postJson("/lab/voice-token", { provider });
}
