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
 *
 * 끝나는 길은 하나(shutdown)입니다: 사용자 종료, 제공사 연결 끊김, 면접 종료(done), 409, 페이지 이탈.
 * 여러 번 불러도 한 번만 정리합니다.
 */
import type { BrainReply, MetricEvent, MetricName } from "@gitinsight/contracts";
import { ApiError, createSession, fetchVoiceToken, isAbort, postMetric, postTurn } from "./api";
import { DELEGATION_TOOL, type VadSettings, sayMessage } from "./config";
import { Meter, epochMs } from "./meter";
import { similarity } from "./similarity";
import type { AdapterHandle, Connect, Provider, TurnKey } from "./types";

export type LogWho = "user" | "ai" | "system" | "error";

export interface SessionUi {
  log(who: LogWho, text: string): void;
  status(text: string): void;
  reply(r: BrainReply): void;
  /** 사용자 말 끝 → 서버가 정한 발화(도구 결과)의 첫소리 (ms) */
  latency(ms: number): void;
  /** 사용자 말 끝 → 도구 결과 전에 나온 AI 첫소리(맞장구) (ms) */
  filler(ms: number): void;
  delegation(ms: number): void;
  bargeIn(): void;
  /** 끼어들기 시작 → AI가 멈춘 시각 (ms). 제한 시간 안에 안 멈추면 null */
  bargeStop(ms: number | null): void;
  /** 받아쓰기와 도구 인자 transcript의 유사도 (0~1). 둘 다 있을 때만 */
  transcriptSimilarity(ratio: number): void;
  /** 음성 AI가 사용자 말에 도구 호출 없이 직접 답했습니다 (위임 누락) */
  delegationMissed(): void;
  /** 서버로 턴 하나를 보냈습니다 (대본 줄 진행용) */
  turnSent(): void;
  /** 면접이 끝났습니다. 이유는 화면에 보여 줄 문구입니다. */
  ended(reason: string): void;
  /**
   * 세션이 정해졌습니다 (새로 만들었거나 이어받음). 음성 연결 전에 불리므로,
   * 연결에 실패해도 화면은 이 세션으로 텍스트 면접을 이어 갈 수 있습니다.
   */
  started?(sessionId: string, current: BrainReply): void;
}

export interface SessionOptions {
  analysisId: string;
  /** 판단 LLM 지연 흉내 (LAB_ENABLED 서버에서만) */
  labDelayMs: number;
  /** /api/metrics에 남길 시나리오 이름. 대본 줄마다 바뀌므로 그때그때 읽습니다. */
  scenario: () => string;
  /** 말 끝 판정 설정 */
  vad: VadSettings;
  /** 도구 호출 전에 짧은 맞장구를 하게 할지 (#28) */
  filler: boolean;
  /** 이 신호가 멈추면 연결 중이든 진행 중이든 면접을 끝냅니다. */
  signal: AbortSignal;
  /**
   * 이미 진행 중인 세션을 이어받습니다 (텍스트 면접 도중 음성으로 바꿀 때).
   * 주면 세션을 새로 만들지 않고, current의 발화부터 음성으로 다시 말합니다.
   * 음성 연결을 닫아도 서버 세션은 끝내지 않습니다.
   */
  existing?: { sessionId: string; current: BrainReply };
}

export interface VoiceSession {
  stop(): void;
}

/** 서버 응답을 못 받았을 때 음성 AI가 할 말. 면접을 진행시키지 않고 다시 말해 달라고만 합니다. */
const RETRY_UTTERANCE = "잠시 연결이 불안정했어요. 방금 답변을 한 번만 더 말씀해 주시겠어요?";
const EMPTY_UTTERANCE = "잘 못 들었어요. 한 번만 더 말씀해 주시겠어요?";
/** 서버가 이미 끝난 세션이라고 답했을 때 (409) */
const ENDED_UTTERANCE = "면접이 끝났습니다. 수고하셨습니다.";

/** 도구 호출이 받아쓰기보다 먼저 오면 이만큼 기다립니다 (OpenAI는 받아쓰기가 늦게 오는 경우가 많음). */
const ASR_WAIT_MS = 1500;
/** 마지막 발화(done)를 말한 뒤 소리가 끝나지 않아도 이 시간이 지나면 정리합니다. */
const END_GRACE_MS = 20_000;
/** 마지막 발화 뒤 이만큼 조용하면 정리합니다. 문장 사이 쉼(측정기 기준 0.4초)에서 끊기지 않게 길게 둡니다. */
const END_QUIET_MS = 1500;

export async function startVoiceSession(
  provider: Provider,
  connect: Connect,
  ui: SessionUi,
  opts: SessionOptions,
): Promise<VoiceSession> {
  // 이 면접 하나의 모든 요청과 연결이 따르는 신호입니다.
  const life = new AbortController();
  let closed = false;
  let handle: AdapterHandle | null = null;
  let meter: Meter | null = null;
  let endTimer: ReturnType<typeof setTimeout> | null = null;
  let endQuietTimer: ReturnType<typeof setTimeout> | null = null;
  let asrWaiter: (() => void) | null = null;

  function shutdown(reason: string) {
    if (closed) return;
    closed = true;
    life.abort(new DOMException(reason, "AbortError"));
    if (endTimer) clearTimeout(endTimer);
    if (endQuietTimer) clearTimeout(endQuietTimer);
    asrWaiter?.();
    meter?.stop();
    handle?.stop();
    ui.status("종료됨");
    ui.ended(reason);
  }
  const onOuterAbort = () => shutdown("사용자가 멈췄습니다");
  if (opts.signal.aborted) onOuterAbort();
  opts.signal.addEventListener("abort", onOuterAbort, { once: true });
  const ensureOpen = () => {
    if (closed) throw new DOMException("면접을 멈췄습니다", "AbortError");
  };

  try {
    ui.status("세션 만드는 중");
    const created = opts.existing
      ? { session_id: opts.existing.sessionId, first: opts.existing.current }
      : await createSession({ analysis_id: opts.analysisId, mode: "voice" }, life.signal);
    ensureOpen();
    const sessionId = created.session_id;
    ui.started?.(sessionId, created.first);
    ui.reply(created.first);
    ui.log("system", `세션 ${sessionId.slice(0, 8)}`);

    const metric = (name: MetricName, extra: Partial<MetricEvent> = {}) =>
      postMetric({ name, session_id: sessionId, at_ms: Date.now(), provider, scenario: opts.scenario(), ...extra });

    ui.status("임시 토큰 받는 중");
    const token = await fetchVoiceToken(provider, opts.vad, opts.filler, life.signal);
    ensureOpen();
    ui.log("system", `모델 ${token.model}`);

    let done = false;
    // 연결이 준비되기 전에 온 도구 호출의 답은 준비될 때까지 보류합니다.
    let resolveHandle!: (h: AdapterHandle) => void;
    const handleReady = new Promise<AdapterHandle>((r) => (resolveHandle = r));
    // 도구 호출은 한 번에 하나씩 처리합니다 (같은 답변이 두 번 넘어가지 않게).
    let queue: Promise<void> = Promise.resolve();

    // ── 받아쓰기 버퍼 ──
    // 턴 키별로 모읍니다. 이미 서버로 보낸 턴의 키로 늦게 온 받아쓰기는 버립니다.
    let asrBuffer: { text: string; key: TurnKey | null }[] = [];
    const sentKeys = new Set<TurnKey>();
    /** 마지막 도구 호출 뒤 사용자 말(받아쓰기)이 있었는가. 이 상태로 AI가 말을 마치면 위임 누락입니다. */
    let userSpokeSinceTool = false;

    // ── 응답 시간 측정 ──
    // 도구 결과를 보낸 뒤 난 AI 첫소리는 서버가 정한 발화(first_audio)로, 기준은 그 턴을 보낼 때의 사용자 말 끝입니다.
    // 사용자가 말한 뒤 도구 결과 전에 난 AI 첫소리는 맞장구(filler_start)로 셉니다.
    let replyPending = false;
    let replyBaseEnd: number | null = null;
    let fillerArmed = false;
    /** done 발화를 보냈고, 그 소리가 끝나면 정리합니다 */
    let endAfterSpeech = false;

    const m = new Meter({
      userStart: (at) => metric("vad_start", { at_ms: at }),
      userEnd: (at) => {
        metric("vad_end", { at_ms: at });
        fillerArmed = true;
      },
      aiStart: (at) => {
        if (endQuietTimer) {
          clearTimeout(endQuietTimer);
          endQuietTimer = null;
        }
        if (m.userSpeaking) return;
        if (replyPending) {
          replyPending = false;
          fillerArmed = false;
          if (replyBaseEnd === null) return;
          const ms = at - replyBaseEnd;
          ui.latency(ms);
          metric("first_audio", { at_ms: at, value_ms: ms });
        } else if (fillerArmed && m.lastUserEnd !== null) {
          fillerArmed = false;
          const ms = at - m.lastUserEnd;
          ui.filler(ms);
          metric("filler_start", { at_ms: at, value_ms: ms });
        }
      },
      aiEnd: () => {
        // 마지막 발화의 소리가 끝나고 한동안 조용하면 면접을 정리합니다.
        if (!endAfterSpeech || replyPending) return;
        if (endQuietTimer) clearTimeout(endQuietTimer);
        endQuietTimer = setTimeout(() => shutdown("면접이 끝났습니다"), END_QUIET_MS);
      },
      bargeIn: () => ui.bargeIn(),
      bargeStop: (bargeAt, stopMs) => {
        ui.bargeStop(stopMs);
        metric("barge_in", stopMs === null ? { at_ms: bargeAt } : { at_ms: bargeAt, value_ms: stopMs });
      },
    });
    meter = m;

    function onUserTranscript(text: string, key: TurnKey | null) {
      ui.log("user", text);
      if (key !== null && sentKeys.has(key)) {
        // 이미 서버로 보낸 턴의 늦은 받아쓰기입니다. 다음 턴에 섞이지 않게 버립니다.
        ui.log("system", "이전 턴의 늦은 받아쓰기라 다음 턴에 쓰지 않습니다");
        return;
      }
      asrBuffer.push({ text, key });
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
      for (const e of asrBuffer) if (e.key !== null) sentKeys.add(e.key);
      asrBuffer = [];
    }

    /**
     * 이 도구 호출에 쓸 받아쓰기를 꺼냅니다.
     * 그 턴의 받아쓰기가 아직 없으면 잠깐 기다립니다. 꺼낸 턴의 키는 보낸 것으로 표시합니다.
     */
    async function takeAsr(key: TurnKey | null): Promise<string> {
      const has = () => asrBuffer.some((e) => key === null || e.key === null || e.key === key);
      if (!has() && !closed) {
        await new Promise<void>((resolve) => {
          const timer = setTimeout(finish, ASR_WAIT_MS);
          function finish() {
            clearTimeout(timer);
            asrWaiter = null;
            resolve();
          }
          asrWaiter = () => {
            if (has() || closed) finish();
          };
        });
      }
      // 아직 보내지 않은 받아쓰기는 모두 이 턴의 것입니다 (제공사가 한 답변을 여러 조각으로 나눠 줄 수 있음).
      const text = asrBuffer
        .map((e) => e.text)
        .join(" ")
        .trim();
      for (const e of asrBuffer) if (e.key !== null) sentKeys.add(e.key);
      if (key !== null) sentKeys.add(key);
      asrBuffer = [];
      // 기다리는 사이 도착한 받아쓰기도 이 턴에 쓰였으므로 위임 누락으로 세지 않습니다.
      userSpokeSinceTool = false;
      return text;
    }

    async function delegate(callId: string, name: string, args: Record<string, unknown>, key: TurnKey | null) {
      userSpokeSinceTool = false;
      // 이 턴의 질문 측정 기준: 도구 호출을 받은 시점의 마지막 사용자 말 끝
      const baseEnd = m.lastUserEnd;
      const respond = (utterance: string, isDone: boolean) => {
        if (closed) return;
        replyPending = true;
        replyBaseEnd = baseEnd;
        if (isDone) {
          endAfterSpeech = true;
          endTimer = setTimeout(() => shutdown("면접이 끝났습니다"), END_GRACE_MS);
        }
        void handleReady.then((h) => {
          if (!closed) h.respondTool(callId, name, { utterance, done: isDone });
        });
      };

      if (name !== DELEGATION_TOOL) {
        ui.log("error", `모르는 도구 호출: ${name}`);
        respond(RETRY_UTTERANCE, false);
        return;
      }
      if (done) {
        respond(ENDED_UTTERANCE, true);
        return;
      }

      const asr = await takeAsr(key);
      if (closed) return;
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
      metric("delegation_start", { turn_id: turnId });
      ui.turnSent();
      try {
        const reply = await postTurn(
          sessionId,
          {
            turn_id: turnId,
            transcript,
            source: "voice",
            speech_started_at: m.lastUserStart,
            speech_ended_at: m.lastUserEnd,
          },
          { labDelayMs: opts.labDelayMs, signal: life.signal },
        );
        done = reply.done;
        ui.reply(reply);
        respond(reply.utterance, reply.done);
      } catch (e) {
        if (closed || isAbort(e)) return;
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
    // 연결에 실패하거나 도중에 멈추면 어댑터가 마이크와 오디오 자원을 정리한 뒤 예외를 던집니다.
    const h = await connect(
      token,
      {
        onToolCall: (callId, name, args, key) => {
          userSpokeSinceTool = false;
          queue = queue
            .then(() => delegate(callId, name, args, key))
            .catch((e: unknown) => ui.log("error", `위임 처리 오류: ${String(e)}`));
        },
        onUserTranscript,
        onAiTranscript,
        onError: (code, message) => {
          ui.log("error", message);
          metric("error", { error_code: code });
        },
        onClosed: (reason) => shutdown(`제공사 연결이 닫혔습니다 (${reason})`),
      },
      { signal: life.signal },
    );
    handle = h;
    if (closed) {
      h.stop();
      throw new DOMException("면접을 멈췄습니다", "AbortError");
    }
    resolveHandle(h);

    m.start(
      () => h.userAnalyser(),
      () => h.aiAnalyser(),
    );
    ui.status("연결됨");
    h.say(sayMessage(created.first.utterance));
  } catch (e) {
    // 시작 도중 실패하거나 멈추면, 열린 것을 모두 닫고 호출한 쪽에 알립니다.
    const reason = isAbort(e) ? "사용자가 멈췄습니다" : "시작하지 못했습니다";
    shutdown(reason);
    opts.signal.removeEventListener("abort", onOuterAbort);
    throw e;
  }

  return {
    stop() {
      opts.signal.removeEventListener("abort", onOuterAbort);
      shutdown("사용자가 멈췄습니다");
    },
  };
}
