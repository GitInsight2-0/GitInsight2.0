/**
 * 음성 면접 진행기. 제공사와 상관없이 같은 순서로 진행합니다.
 *
 * 1. FastAPI에 세션을 만들고 첫 발화(BrainReply)를 받습니다.
 * 2. 임시 토큰으로 음성 제공사에 연결하고 첫 발화를 말하게 합니다.
 * 3. 음성 AI가 submit_turn 도구를 부르면 /turns로 보내고, 받은 utterance를 도구 결과로 돌려줍니다 (위임).
 * 4. 측정 이벤트를 /api/metrics로 보냅니다.
 *
 * 다음 질문, 반복, 힌트, 종료는 모두 서버가 정합니다. 여기서는 서버 응답을 전달만 합니다.
 */
import type { BrainReply, MetricEvent, MetricName } from "@gitinsight/contracts";
import { createSession, fetchVoiceToken, postMetric, postTurn } from "./api";
import { DELEGATION_TOOL, sayMessage } from "./config";
import { Meter, epochMs } from "./meter";
import type { AdapterHandle, Connect, Provider } from "./types";

export type LogWho = "user" | "ai" | "system" | "error";

export interface SessionUi {
  log(who: LogWho, text: string): void;
  status(text: string): void;
  reply(r: BrainReply): void;
  latency(ms: number): void;
  delegation(ms: number): void;
  bargeIn(): void;
}

export interface SessionOptions {
  analysisId: string;
  /** 판단 LLM 지연 흉내 (LAB_ENABLED 서버에서만) */
  labDelayMs: number;
  /** /api/metrics에 남길 시나리오 이름 */
  scenario: string;
}

/** 서버 응답을 못 받았을 때 음성 AI가 할 말. 면접을 진행시키지 않고 다시 말해 달라고만 합니다. */
const RETRY_UTTERANCE = "잠시 연결이 불안정했어요. 방금 답변을 한 번만 더 말씀해 주시겠어요?";
const EMPTY_UTTERANCE = "잘 못 들었어요. 한 번만 더 말씀해 주시겠어요?";

export async function startVoiceSession(
  provider: Provider,
  connect: Connect,
  ui: SessionUi,
  opts: SessionOptions,
): Promise<{ stop(): void }> {
  ui.status("세션 만드는 중");
  const created = await createSession({ analysis_id: opts.analysisId, mode: "voice" });
  const sessionId = created.session_id;
  ui.reply(created.first);
  ui.log("system", `세션 ${sessionId.slice(0, 8)}`);

  const metric = (name: MetricName, extra: Partial<MetricEvent> = {}) =>
    postMetric({ name, session_id: sessionId, at_ms: Date.now(), provider, scenario: opts.scenario, ...extra });

  ui.status("임시 토큰 받는 중");
  const token = await fetchVoiceToken(provider);
  ui.log("system", `모델 ${token.model}`);

  let done = false;
  let lastUserTranscript = "";
  let handle: AdapterHandle | null = null;
  // 도구 호출은 한 번에 하나씩 처리합니다 (같은 답변이 두 번 넘어가지 않게).
  let queue: Promise<void> = Promise.resolve();

  const meter = new Meter({
    userStart: (at) => metric("vad_start", { at_ms: at }),
    userEnd: (at) => metric("vad_end", { at_ms: at }),
    aiStart: (at, latency) => {
      if (latency === null) return;
      ui.latency(latency);
      metric("first_audio", { at_ms: at, value_ms: latency });
    },
    bargeIn: (at) => {
      ui.bargeIn();
      metric("barge_in", { at_ms: at });
    },
  });

  async function delegate(callId: string, name: string, args: Record<string, unknown>) {
    const respond = (utterance: string, isDone: boolean) =>
      handle?.respondTool(callId, name, { utterance, done: isDone });

    if (name !== DELEGATION_TOOL) {
      ui.log("error", `모르는 도구 호출: ${name}`);
      respond(RETRY_UTTERANCE, false);
      return;
    }
    if (done) {
      respond("면접이 끝났습니다.", true);
      return;
    }
    const fromModel = typeof args.transcript === "string" ? args.transcript.trim() : "";
    const transcript = fromModel || lastUserTranscript.trim();
    lastUserTranscript = "";
    if (!transcript) {
      respond(EMPTY_UTTERANCE, false);
      return;
    }

    const turnId = crypto.randomUUID();
    const t0 = performance.now();
    metric("delegation_start", { turn_id: turnId });
    try {
      const reply = await postTurn(
        sessionId,
        {
          turn_id: turnId,
          transcript,
          source: "voice",
          speech_started_at: meter.lastUserStart,
          speech_ended_at: meter.lastUserEnd,
        },
        { labDelayMs: opts.labDelayMs },
      );
      done = reply.done;
      ui.reply(reply);
      respond(reply.utterance, reply.done);
    } catch (e) {
      ui.log("error", `서버 응답 실패: ${String(e)}`);
      metric("error", { turn_id: turnId, error_code: "turn_failed" });
      respond(RETRY_UTTERANCE, false);
    } finally {
      const ms = Math.round(performance.now() - t0);
      ui.delegation(ms);
      metric("delegation_end", { turn_id: turnId, at_ms: epochMs(performance.now()), value_ms: ms });
    }
  }

  ui.status(`${provider} 연결 중`);
  handle = await connect(token, {
    onToolCall: (callId, name, args) => {
      queue = queue.then(() => delegate(callId, name, args));
    },
    onUserTranscript: (text) => {
      lastUserTranscript += ` ${text}`;
      ui.log("user", text);
    },
    onAiTranscript: (text, interrupted) => ui.log("ai", interrupted ? `${text} (끊김)` : text),
    onError: (code, message) => {
      ui.log("error", message);
      metric("error", { error_code: code });
    },
    onClosed: (reason) => {
      ui.log("system", `연결 종료: ${reason}`);
      ui.status("종료됨");
    },
  });

  meter.start(
    () => handle?.userAnalyser() ?? null,
    () => handle?.aiAnalyser() ?? null,
  );
  ui.status("연결됨");
  handle.say(sayMessage(created.first.utterance));

  return {
    stop() {
      meter.stop();
      handle?.stop();
      ui.status("종료됨");
    },
  };
}
