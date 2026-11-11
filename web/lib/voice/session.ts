/**
 * 음성 면접 진행기. 제공사와 상관없이 같은 순서로 진행합니다.
 *
 * 1. FastAPI에 세션을 만들고 첫 발화(BrainReply)를 받습니다.
 * 2. 임시 토큰으로 음성 제공사에 연결하고 첫 발화를 말하게 합니다.
 * 3. 음성 AI가 submit_turn 도구를 부르면 /turns로 보내고, 받은 utterance를 도구 결과로 돌려줍니다 (위임).
 * 4. 측정 이벤트를 /api/metrics로 보냅니다.
 *
 * 다음 질문, 반복, 힌트, 종료는 모두 서버가 정합니다. 여기서는 서버 응답을 전달만 합니다.
 * 서버로 보내는 transcript는 받아쓰기(ASR)를 먼저 씁니다.
 * 음성 AI가 도구 인자로 준 transcript는 요약되거나 다듬어졌을 수 있어서, 받아쓰기가 없을 때만 씁니다.
 */
import type { BrainReply, MetricEvent, MetricName } from "@gitinsight/contracts";
import { ApiError, createSession, fetchVoiceToken, postMetric, postTurn } from "./api";
import { DELEGATION_TOOL, type VadSettings, sayMessage } from "./config";
import { Meter, epochMs } from "./meter";
import { similarity } from "./similarity";
import type { AdapterHandle, Connect, Provider } from "./types";

export type LogWho = "user" | "ai" | "system" | "error";

export interface SessionUi {
  log(who: LogWho, text: string): void;
  status(text: string): void;
  reply(r: BrainReply): void;
  latency(ms: number): void;
  delegation(ms: number): void;
  bargeIn(): void;
  /** 받아쓰기와 도구 인자 transcript의 유사도 (0~1). 둘 다 있을 때만 */
  transcriptSimilarity(ratio: number): void;
  /** 음성 AI가 사용자 말에 도구 호출 없이 직접 답했습니다 (위임 누락) */
  delegationMissed(): void;
}

export interface SessionOptions {
  analysisId: string;
  /** 판단 LLM 지연 흉내 (LAB_ENABLED 서버에서만) */
  labDelayMs: number;
  /** /api/metrics에 남길 시나리오 이름 */
  scenario: string;
  /** 말 끝 판정 설정 */
  vad: VadSettings;
}

/** 서버 응답을 못 받았을 때 음성 AI가 할 말. 면접을 진행시키지 않고 다시 말해 달라고만 합니다. */
const RETRY_UTTERANCE = "잠시 연결이 불안정했어요. 방금 답변을 한 번만 더 말씀해 주시겠어요?";
const EMPTY_UTTERANCE = "잘 못 들었어요. 한 번만 더 말씀해 주시겠어요?";
/** 서버가 이미 끝난 세션이라고 답했을 때 (409) */
const ENDED_UTTERANCE = "면접이 끝났습니다. 수고하셨습니다.";

/** 도구 호출이 받아쓰기보다 먼저 오면 이만큼 기다립니다 (OpenAI는 받아쓰기가 늦게 오는 경우가 많음). */
const ASR_WAIT_MS = 1500;
/** 턴을 보낸 뒤 이 시간 안에 새 발화 없이 도착한 받아쓰기는 이전 턴 것으로 보고 버립니다. */
const LATE_ASR_WINDOW_MS = 5000;

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
  const token = await fetchVoiceToken(provider, opts.vad);
  ui.log("system", `모델 ${token.model}`);

  let done = false;
  let handle: AdapterHandle | null = null;
  // 도구 호출은 한 번에 하나씩 처리합니다 (같은 답변이 두 번 넘어가지 않게).
  let queue: Promise<void> = Promise.resolve();

  // ── 받아쓰기 버퍼 ──
  // 마지막으로 턴을 보낸 뒤 들어온 받아쓰기만 모읍니다.
  let asrBuffer: string[] = [];
  let asrWaiter: (() => void) | null = null;
  /** 마지막으로 턴을 보낸 시각 (epoch ms). 0이면 아직 없음 */
  let lastSentAt = 0;
  /** 마지막 도구 호출 뒤 사용자 말(받아쓰기)이 있었는가. 이 상태로 AI가 말을 마치면 위임 누락입니다. */
  let userSpokeSinceTool = false;

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

  function onUserTranscript(text: string) {
    ui.log("user", text);
    const waiting = asrWaiter !== null;
    const now = Date.now();
    const speechSinceSent = meter.lastUserStart !== null && meter.lastUserStart > lastSentAt;
    // 턴을 보낸 직후, 새 발화도 없는데 도착한 받아쓰기는 이전 턴의 늦은 결과입니다.
    // 다음 턴에 섞이지 않게 버립니다. 마이크 측정이 아예 안 된 경우(lastUserStart가 null)는 버리지 않습니다.
    const late =
      !waiting && lastSentAt > 0 && now - lastSentAt < LATE_ASR_WINDOW_MS && meter.lastUserStart !== null && !speechSinceSent;
    if (late) {
      ui.log("system", "이전 턴의 늦은 받아쓰기라 다음 턴에 쓰지 않습니다");
      return;
    }
    asrBuffer.push(text);
    userSpokeSinceTool = true;
    asrWaiter?.();
  }

  function onAiTranscript(text: string, interrupted: boolean) {
    ui.log("ai", interrupted ? `${text} (끊김)` : text);
    // 사용자가 말한 뒤 도구 호출 없이 AI가 말을 끝까지 했다면, 서버 대신 모델이 직접 답한 것입니다.
    // 끊긴 발화는 사용자가 끼어든 경우라 세지 않습니다.
    if (interrupted || !userSpokeSinceTool) return;
    userSpokeSinceTool = false;
    ui.delegationMissed();
    ui.log("system", "위임 누락: 음성 AI가 서버를 부르지 않고 직접 답했습니다");
    metric("error", { error_code: "delegation_missed" });
    // 모델이 직접 처리한 말은 다음 답변에 섞이지 않게 버립니다.
    asrBuffer = [];
  }

  /** 받아쓰기가 아직 없으면 잠깐 기다린 뒤, 모인 받아쓰기를 꺼냅니다. */
  async function takeAsr(): Promise<string> {
    if (asrBuffer.length === 0) {
      await new Promise<void>((resolve) => {
        const timer = setTimeout(finish, ASR_WAIT_MS);
        function finish() {
          clearTimeout(timer);
          asrWaiter = null;
          resolve();
        }
        asrWaiter = finish;
      });
    }
    const text = asrBuffer.join(" ").trim();
    asrBuffer = [];
    // 기다리는 사이 도착한 받아쓰기도 이 턴에 쓰였으므로 위임 누락으로 세지 않습니다.
    userSpokeSinceTool = false;
    return text;
  }

  async function delegate(callId: string, name: string, args: Record<string, unknown>) {
    userSpokeSinceTool = false;
    const respond = (utterance: string, isDone: boolean) =>
      handle?.respondTool(callId, name, { utterance, done: isDone });

    if (name !== DELEGATION_TOOL) {
      ui.log("error", `모르는 도구 호출: ${name}`);
      respond(RETRY_UTTERANCE, false);
      return;
    }
    if (done) {
      respond(ENDED_UTTERANCE, true);
      return;
    }

    const asr = await takeAsr();
    const fromModel = typeof args.transcript === "string" ? args.transcript.trim() : "";
    const transcript = asr || fromModel;
    if (asr && fromModel) ui.transcriptSimilarity(similarity(asr, fromModel));
    if (!asr && fromModel) ui.log("system", "받아쓰기가 없어 음성 AI의 도구 인자를 보냅니다");
    if (!transcript) {
      respond(EMPTY_UTTERANCE, false);
      return;
    }

    const turnId = crypto.randomUUID();
    const t0 = performance.now();
    lastSentAt = Date.now();
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
      if (e instanceof ApiError && e.status === 409) {
        // 서버가 이미 끝난 세션이라고 답했습니다. 다시 말해 달라고 하지 않고 종료합니다.
        done = true;
        ui.log("system", "서버가 이미 끝난 세션이라고 답했습니다");
        respond(ENDED_UTTERANCE, true);
        return;
      }
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
  // 연결에 실패하면 어댑터가 마이크와 오디오 자원을 정리한 뒤 예외를 던집니다.
  handle = await connect(token, {
    onToolCall: (callId, name, args) => {
      userSpokeSinceTool = false;
      queue = queue
        .then(() => delegate(callId, name, args))
        .catch((e: unknown) => ui.log("error", `위임 처리 오류: ${String(e)}`));
    },
    onUserTranscript,
    onAiTranscript,
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
