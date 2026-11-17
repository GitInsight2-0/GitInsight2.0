/** FastAPI 면접 API 호출. 경로와 형식은 contracts의 계약을 따릅니다. */
import type { BrainReply, MetricEvent, SessionCreate, SessionCreated, SessionEnded, TurnRequest, VoiceToken } from "@gitinsight/contracts";
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
/**
 * 턴 하나를 기다리는 전체 시간 (재시도 포함, 판단 지연 흉내는 따로 더함).
 * 런북의 "10초 넘게 반응이 없으면 다음 대비책" 규칙 안에 들어오게 둡니다.
 * 서버는 판단 LLM을 4초 안에 끝내고 대비책으로 넘어가므로 늦어도 5초쯤에는 답합니다.
 */
const TURN_BUDGET_MS = 9000;
/** 첫 시도의 제한 시간. 남은 시간은 같은 turn_id로 한 번 더 보내는 데 씁니다 */
const TURN_FIRST_TRY_MS = 6000;
/** 계약 TurnRequest.transcript의 최대 길이 */
export const TRANSCRIPT_MAX = 4000;

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
  // 길이 상한을 넘으면 서버가 422로 거절하므로 미리 자릅니다.
  if (body.transcript.length > TRANSCRIPT_MAX) body = { ...body, transcript: cut(body.transcript, TRANSCRIPT_MAX) };
  const lab = opts.labDelayMs ?? 0;
  const started = performance.now();
  for (let attempt = 0; ; attempt++) {
    // 판단 지연 흉내(lab)는 시도마다 서버가 더 기다리므로 예산에도 시도마다 더합니다.
    const left = TURN_BUDGET_MS + lab * (attempt + 1) - (performance.now() - started);
    const limit = attempt === 0 ? TURN_FIRST_TRY_MS : left - lab;
    const s = linkedSignal(opts.signal, limit + lab);
    try {
      return await postJson<BrainReply>(url, body, s.signal);
    } catch (e) {
      if (opts.signal?.aborted) throw e;
      const retryable = !(e instanceof ApiError) || e.status >= 500;
      // 남은 시간이 너무 짧으면 다시 보내도 받을 수 없으니 바로 실패로 넘깁니다.
      // 다음 시도가 쓸 수 있는 시간 (그 시도의 lab 지연 제외)
      const left = TURN_BUDGET_MS + lab * (attempt + 1) - (performance.now() - started);
      if (attempt >= 1 || !retryable || left < 1000) throw e;
    } finally {
      s.done();
    }
  }
}

/**
 * 리포트 만들기를 기다리는 시간.
 * 서버는 리포트 LLM을 30초(report_timeout_s), 꼬리 판단 다시 하기를 10초(rejudge_timeout_s) 안에 끊고 대비책 리포트를 냅니다.
 */
const END_TIMEOUT_MS = 45_000;
/**
 * 리포트 요청을 보내는 최대 횟수.
 * Next 중계(rewrites)는 30초에 끊고 본문 없는 500을 돌려주는데, 서버는 하던 리포트를 마저 만들어 저장합니다 (#78).
 * 그래서 다시 부르면 만들어 둔 리포트를 받거나, 아직 만드는 중이면 끝날 때까지 기다렸다 받습니다.
 */
const END_ATTEMPTS = 3;
/** 다시 부르기 전에 쉬는 시간 */
const END_RETRY_DELAY_MS = 2000;

/**
 * 세션을 끝내고 리포트 id를 받습니다.
 * 서버는 여러 번 불러도 같은 report_id를 돌려주므로, 5xx나 연결 실패면 같은 요청을 다시 보냅니다.
 * 503은 리포트를 만들지 않는 서버라 다시 보내지 않습니다. 바깥 신호로 멈추면 다시 보내지 않습니다.
 */
export async function endSession(sessionId: string, outer?: AbortSignal): Promise<SessionEnded> {
  for (let attempt = 1; ; attempt++) {
    const s = linkedSignal(outer, END_TIMEOUT_MS);
    try {
      return await postJson<SessionEnded>(`/api/sessions/${sessionId}/end`, {}, s.signal);
    } catch (e) {
      if (outer?.aborted) throw e;
      const retryable = !(e instanceof ApiError) || (e.status >= 500 && e.status !== 503);
      if (attempt >= END_ATTEMPTS || !retryable) throw e;
    } finally {
      s.done();
    }
    await wait(END_RETRY_DELAY_MS, outer);
  }
}

/** ms만큼 기다립니다. 바깥 신호로 멈추면 그 이유로 바로 실패합니다 */
function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
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
  vad: VadSettings,
  filler: boolean,
  outer?: AbortSignal,
): Promise<VoiceToken> {
  // TODO: FastAPI의 POST /api/sessions/{id}/voice-token이 생기면 그쪽으로 바꿉니다.
  const s = linkedSignal(outer, SETUP_TIMEOUT_MS);
  try {
    return await postJson("/voice-token", { vad, filler }, s.signal);
  } finally {
    s.done();
  }
}

/** UTF-16 단위로 max까지 자르되, 이모지 같은 서로게이트 쌍의 가운데에서 끊지 않습니다 */
function cut(text: string, max: number): string {
  const last = text.charCodeAt(max - 1);
  return text.slice(0, last >= 0xd800 && last <= 0xdbff ? max - 1 : max);
}
