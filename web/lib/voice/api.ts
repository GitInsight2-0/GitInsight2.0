/** FastAPI 면접 API 호출. 경로와 형식은 contracts의 계약을 따릅니다. */
import type { BrainReply, MetricEvent, SessionCreate, SessionCreated, TurnRequest, VoiceToken } from "@gitinsight/contracts";
import type { VadSettings } from "./config";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`${status}: ${detail}`);
  }
}

/** 세션 생성, 토큰 요청의 제한 시간 */
export const SETUP_TIMEOUT_MS = 10_000;
/** 턴 한 번의 제한 시간 (판단 지연 흉내는 따로 더함) */
const TURN_TIMEOUT_MS = 8000;

/**
 * 바깥 신호(사용자 종료)와 제한 시간을 함께 따르는 AbortSignal을 만듭니다.
 * 끝나면 done()을 불러 타이머와 연결을 풉니다.
 */
export function linkedSignal(outer: AbortSignal | undefined, timeoutMs: number): { signal: AbortSignal; done(): void } {
  const ctrl = new AbortController();
  const onAbort = () => ctrl.abort(outer?.reason);
  if (outer?.aborted) ctrl.abort(outer.reason);
  else outer?.addEventListener("abort", onAbort, { once: true });
  const timer = setTimeout(() => ctrl.abort(new DOMException("제한 시간을 넘었습니다", "TimeoutError")), timeoutMs);
  return {
    signal: ctrl.signal,
    done() {
      clearTimeout(timer);
      outer?.removeEventListener("abort", onAbort);
    },
  };
}

/** 사용자가 직접 멈춘 경우인지 (오류로 기록하지 않습니다) */
export function isAbort(e: unknown): boolean {
  return (e as { name?: unknown } | null)?.name === "AbortError";
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
    // Next가 FastAPI에 닿지 못하면 본문 없는 500을 돌려줍니다.
    if (r.status >= 500 && data === null && url.startsWith("/api/")) {
      throw new ApiError(r.status, "API 서버에 연결할 수 없습니다. 터미널 1에서 API 서버가 켜져 있는지 확인하세요.");
    }
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new ApiError(r.status, typeof detail === "string" ? detail : JSON.stringify(detail ?? data));
  }
  return data as T;
}

export async function createSession(body: SessionCreate, outer?: AbortSignal): Promise<SessionCreated> {
  const s = linkedSignal(outer, SETUP_TIMEOUT_MS);
  try {
    return await postJson("/api/sessions", body, s.signal);
  } finally {
    s.done();
  }
}

/**
 * 답변을 보내고 다음 발화를 받습니다.
 * turn_id가 같으면 서버가 처음 결과를 그대로 돌려주므로, 시간 초과나 네트워크 오류 때 같은 turn_id로 한 번 더 보냅니다.
 * 바깥 신호로 멈추면 다시 보내지 않습니다.
 */
export async function postTurn(
  sessionId: string,
  body: TurnRequest,
  opts: { labDelayMs?: number; signal?: AbortSignal } = {},
): Promise<BrainReply> {
  const q = opts.labDelayMs ? `?lab_delay_ms=${opts.labDelayMs}` : "";
  const url = `/api/sessions/${sessionId}/turns${q}`;
  for (let attempt = 0; ; attempt++) {
    const s = linkedSignal(opts.signal, TURN_TIMEOUT_MS + (opts.labDelayMs ?? 0));
    try {
      return await postJson<BrainReply>(url, body, s.signal);
    } catch (e) {
      if (opts.signal?.aborted) throw e;
      const retryable = !(e instanceof ApiError) || e.status >= 500;
      if (attempt >= 1 || !retryable) throw e;
    } finally {
      s.done();
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

export async function fetchVoiceToken(
  provider: VoiceToken["provider"],
  vad: VadSettings,
  filler: boolean,
  outer?: AbortSignal,
): Promise<VoiceToken> {
  // TODO: FastAPI의 POST /api/sessions/{id}/voice-token이 생기면 그쪽으로 바꿉니다.
  const s = linkedSignal(outer, SETUP_TIMEOUT_MS);
  try {
    return await postJson("/lab/voice-token", { provider, vad, filler }, s.signal);
  } finally {
    s.done();
  }
}
