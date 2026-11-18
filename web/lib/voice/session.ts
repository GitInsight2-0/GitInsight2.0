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
import { DELEGATION_TOOL, SAY_MARKER, type VadSettings, sayMessage } from "./config";
import { Meter, epochMs } from "./meter";
import { similarity } from "./similarity";
import { type AdapterHandle, type Connect, PROVIDER, type TurnKey } from "./types";

export type LogWho = "user" | "ai" | "system" | "error";

/**
 * 음성이 끝난 까닭.
 * done: 면접이 끝남 (서버 done, 409 이미 끝난 세션). user: 사용자나 화면이 멈춤. dropped: 연결 끊김, 시작 실패 등.
 */
export type EndKind = "done" | "user" | "dropped";

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
  /** 서버로 턴 하나를 보냈습니다. transcript는 실제로 보낸 답변입니다 (대본 줄 진행, 대화 기록용) */
  turnSent(transcript: string): void;
  /**
   * 이번 턴에서 지금까지 받아쓴 답변 전체 (말하는 동안 계속 갱신).
   * 이미 보낸 턴의 늦은 조각은 들어가지 않습니다. 화면 표시용이고, 서버로 보낸 답변은 turnSent로 받습니다.
   */
  answer?(textSoFar: string): void;
  /** 음성이 끝났습니다. reason은 화면에 보여 줄 문구, kind는 끝난 까닭입니다. */
  ended(reason: string, kind: EndKind): void;
  /**
   * 세션이 정해졌습니다 (새로 만들었거나 이어받음). 음성 연결 전에 불리므로,
   * 연결에 실패해도 화면은 이 세션으로 텍스트 면접을 이어 갈 수 있습니다.
   */
  started?(sessionId: string, current: BrainReply): void;
  /** 누가 소리를 내기 시작하거나 멈췄습니다 (측정기 기준). 화면의 듣는 중, 말하는 중 표시에 씁니다. */
  speaking?(who: "user" | "ai", on: boolean): void;
  /** 음성 연결이 열렸습니다. 화면이 소리 크기를 그릴 수 있게 측정용 노드를 넘깁니다. */
  audio?(levels: AudioLevels): void;
}

/** 측정용 소리 노드. 연결 중에는 null일 수 있어 그때그때 꺼내 씁니다. */
export interface AudioLevels {
  user(): AnalyserNode | null;
  ai(): AnalyserNode | null;
}

export interface SessionOptions {
  analysisId: string;
  /** 다시 보기 면접: 지난 면접에서 답변이 부족했던 주제만 묻습니다 (#107) */
  reviewOnly?: boolean;
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
/** 턴이 이만큼 연달아 실패하면 같은 부탁을 반복하지 않고 음성을 끝냅니다 (화면은 텍스트로 이어 갑니다) */
const MAX_TURN_FAILURES = 2;
/** 턴이 연달아 실패해 음성을 끝내기 전에 할 말. 말없이 끊기면 사용자가 무슨 일인지 모릅니다 */
const DROP_UTTERANCE = "연결이 계속 불안정해서 텍스트로 이어 가겠습니다. 답변은 화면에 적어 주세요.";
const DROP_END = { reason: "서버 응답이 계속 실패해 음성을 멈췄습니다", kind: "dropped" } as const;
/** 서버가 이미 끝난 세션이라고 답했을 때 (409) */
const ENDED_UTTERANCE = "면접이 끝났습니다. 수고하셨습니다.";

/** 도구 호출이 받아쓰기보다 먼저 오면 이만큼 기다립니다 (받아쓰기가 도구 호출보다 늦게 오는 경우가 있음). */
const ASR_WAIT_MS = 1500;
/** 마지막 발화(done)를 말한 뒤 소리가 끝나지 않아도 이 시간이 지나면 정리합니다. */
const END_GRACE_MS = 20_000;
/** 마지막 발화 뒤 이만큼 조용하면 정리합니다. 문장 사이 쉼(측정기 기준 0.4초)에서 끊기지 않게 길게 둡니다. */
const END_QUIET_MS = 1500;
/**
 * 도구 결과를 보낸 뒤 이만큼 AI 소리가 없으면 같은 발화를 say로 한 번 더 보냅니다 (#86).
 * Gemini Live가 도구 결과를 받고도 말하지 않는 세션이 있습니다. 평소 첫소리는 1초 안쪽입니다.
 */
const SPEAK_FALLBACK_MS = 3000;
/** 도구 인자가 다시 보낸 발화와 이만큼 비슷하면 사용자 답변이 아니라 그 발화를 넘긴 것으로 봅니다 */
const ECHO_SIMILARITY = 0.6;

export async function startVoiceSession(
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
  /** 도구 결과를 말하지 않을 때 다시 보내는 타이머 (#86) */
  let speakFallbackTimer: ReturnType<typeof setTimeout> | null = null;
  /** 다시 보내기로 say한 발화. 음성 AI가 이걸 답변처럼 도구로 넘기면 걸러 냅니다 */
  let resentUtterance: string | null = null;
  /** 걸어 둔 다시 보내기를 기다리지 않고 바로 실행합니다. 걸어 둔 것이 없으면 null */
  let speakFallbackNow: (() => void) | null = null;
  const clearSpeakFallback = () => {
    if (speakFallbackTimer) clearTimeout(speakFallbackTimer);
    speakFallbackTimer = null;
    speakFallbackNow = null;
  };

  function shutdown(reason: string, kind: EndKind) {
    if (closed) return;
    closed = true;
    life.abort(new DOMException(reason, "AbortError"));
    if (endTimer) clearTimeout(endTimer);
    if (endQuietTimer) clearTimeout(endQuietTimer);
    clearSpeakFallback();
    asrWaiter?.();
    meter?.stop();
    handle?.stop();
    ui.status("종료됨");
    ui.ended(reason, kind);
  }
  const onOuterAbort = () => shutdown("사용자가 멈췄습니다", "user");
  if (opts.signal.aborted) onOuterAbort();
  opts.signal.addEventListener("abort", onOuterAbort, { once: true });
  const ensureOpen = () => {
    if (closed) throw new DOMException("면접을 멈췄습니다", "AbortError");
  };

  try {
    ui.status("세션 만드는 중");
    const created = opts.existing
      ? { session_id: opts.existing.sessionId, first: opts.existing.current }
      : await createSession(
          { analysis_id: opts.analysisId, mode: "voice", review_only: opts.reviewOnly ?? false },
          life.signal,
        );
    ensureOpen();
    const sessionId = created.session_id;
    ui.started?.(sessionId, created.first);
    ui.reply(created.first);
    ui.log("system", `세션 ${sessionId.slice(0, 8)}`);

    const metric = (name: MetricName, extra: Partial<MetricEvent> = {}) =>
      postMetric({ name, session_id: sessionId, at_ms: Date.now(), provider: PROVIDER, scenario: opts.scenario(), ...extra });

    ui.status("임시 토큰 받는 중");
    const token = await fetchVoiceToken(opts.vad, opts.filler, life.signal);
    ensureOpen();
    ui.log("system", `모델 ${token.model}`);

    let done = false;
    /** 연달아 실패한 턴 수. 성공하면 0으로 돌아갑니다 */
    let turnFailures = 0;
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
    /** 마지막 발화 뒤 정리할 때 화면에 알릴 까닭. 면접 종료가 기본이고, 연속 실패면 dropped입니다 */
    let endAs: { reason: string; kind: EndKind } = { reason: "면접이 끝났습니다", kind: "done" };
    /** 연속 실패로 음성을 끝내는 중. 이후 도구 호출에는 같은 안내만 합니다 */
    let dropping = false;

    const m = new Meter({
      userStart: (at) => {
        // 사용자가 먼저 말을 시작했으면 질문을 다시 들이밀지 않습니다.
        clearSpeakFallback();
        ui.speaking?.("user", true);
        metric("vad_start", { at_ms: at });
      },
      userEnd: (at) => {
        ui.speaking?.("user", false);
        metric("vad_end", { at_ms: at });
        fillerArmed = true;
      },
      aiStart: (at) => {
        clearSpeakFallback();
        ui.speaking?.("ai", true);
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
        ui.speaking?.("ai", false);
        // 맞장구 소리 때문에 미뤄 둔 다시 보내기가 있으면, 소리가 끝난 뒤부터 다시 기다립니다 (#86).
        if (speakFallbackNow && !speakFallbackTimer) speakFallbackTimer = setTimeout(speakFallbackNow, SPEAK_FALLBACK_MS);
        // 마지막 발화의 소리가 끝나고 한동안 조용하면 면접을 정리합니다.
        if (!endAfterSpeech || replyPending) return;
        if (endQuietTimer) clearTimeout(endQuietTimer);
        endQuietTimer = setTimeout(() => shutdown(endAs.reason, endAs.kind), END_QUIET_MS);
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
      showAnswer();
      userSpokeSinceTool = true;
      asrWaiter?.();
    }

    /** 이번 턴에서 덩어리로 받은 받아쓰기와, 아직 덩어리가 되지 않은 말(partial)을 이어 화면에 알립니다 */
    function showAnswer(partial = "") {
      const parts = asrBuffer.map((e) => e.text);
      if (partial) parts.push(partial);
      ui.answer?.(parts.join(" "));
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

    /**
     * 도구 결과를 보낸 뒤 AI 소리가 안 나면, 같은 발화를 첫 질문과 같은 방식(say)으로 한 번 더 보냅니다 (#86).
     * 그 사이 AI가 말을 시작하거나 사용자가 말을 시작하면 측정기 콜백이 타이머를 지웁니다.
     */
    function armSpeakFallback(h: AdapterHandle, utterance: string) {
      clearSpeakFallback();
      const fire = () => {
        clearSpeakFallback();
        if (closed || m.userSpeaking) return;
        // 앞서 나던 소리(맞장구 등)가 아직 재생 중이면 버리지 않고, 그 소리가 끝날 때(aiEnd) 다시 기다립니다.
        if (m.aiSpeaking) {
          speakFallbackNow = fire;
          return;
        }
        ui.log("system", "음성 AI가 도구 결과를 말하지 않아 같은 문장을 다시 보냅니다");
        metric("error", { error_code: "tool_reply_silent" });
        resentUtterance = utterance;
        h.say(sayMessage(utterance));
      };
      speakFallbackNow = fire;
      speakFallbackTimer = setTimeout(fire, SPEAK_FALLBACK_MS);
    }

    /** 다시 보낸 발화(say)를 음성 AI가 사용자 답변처럼 도구로 넘긴 것인지 */
    function isResentEcho(fromModel: string): boolean {
      if (resentUtterance === null || !fromModel) return false;
      return fromModel.includes(SAY_MARKER) || similarity(fromModel, resentUtterance) >= ECHO_SIMILARITY;
    }

    async function delegate(callId: string, name: string, args: Record<string, unknown>, key: TurnKey | null) {
      // 앞 발화의 다시 보내기가 남아 있으면 지웁니다. 질문 두 개가 이어서 들리지 않게 합니다 (#86).
      clearSpeakFallback();
      userSpokeSinceTool = false;
      // 이 턴의 질문 측정 기준: 도구 호출을 받은 시점의 마지막 사용자 말 끝
      const baseEnd = m.lastUserEnd;
      /** fallback이 false면 이 발화에는 다시 보내기를 걸지 않습니다 (같은 일이 되풀이되지 않게) */
      const respond = (utterance: string, isDone: boolean, end?: { reason: string; kind: EndKind }, fallback = true) => {
        if (closed) return;
        replyPending = true;
        replyBaseEnd = baseEnd;
        if (end) endAs = end;
        if (isDone) {
          endAfterSpeech = true;
          endTimer = setTimeout(() => shutdown(endAs.reason, endAs.kind), END_GRACE_MS);
        }
        void handleReady.then((h) => {
          if (closed) return;
          h.respondTool(callId, name, { utterance, done: isDone });
          if (fallback) armSpeakFallback(h, utterance);
        });
      };

      if (name !== DELEGATION_TOOL) {
        ui.log("error", `모르는 도구 호출: ${name}`);
        respond(RETRY_UTTERANCE, false);
        return;
      }
      if (dropping) {
        respond(DROP_UTTERANCE, true, DROP_END);
        return;
      }
      if (done) {
        respond(ENDED_UTTERANCE, true);
        return;
      }

      const asr = await takeAsr(key);
      if (closed) return;
      const fromModel = typeof args.transcript === "string" ? args.transcript.trim() : "";
      // 사용자는 말하지 않았는데, 다시 보낸 질문을 음성 AI가 답변처럼 넘겼습니다 (#86).
      // 서버로 보내면 답하지 않은 질문이 넘어가므로, 보내지 않고 같은 질문을 말하게 합니다.
      if (!asr && isResentEcho(fromModel)) {
        const again = resentUtterance ?? "";
        resentUtterance = null;
        ui.log("system", "음성 AI가 다시 보낸 질문을 답변으로 넘겨서 서버로 보내지 않습니다");
        metric("error", { error_code: "tool_reply_echo" });
        respond(again, false, undefined, false);
        return;
      }
      resentUtterance = null;
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
      ui.turnSent(transcript);
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
        turnFailures = 0;
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
        turnFailures++;
        if (turnFailures >= MAX_TURN_FAILURES) {
          // 같은 답변을 계속 거절당하거나 서버가 계속 늦으면, 다시 말해 달라는 부탁만 반복하게 됩니다.
          // 짧게 안내한 뒤(마지막 발화와 같은 정리 경로) 음성을 끝내고, 화면은 텍스트로 이어 갑니다.
          dropping = true;
          respond(DROP_UTTERANCE, true, DROP_END);
          return;
        }
        respond(RETRY_UTTERANCE, false);
      } finally {
        const ms = Math.round(performance.now() - t0);
        ui.delegation(ms);
        metric("delegation_end", { turn_id: turnId, at_ms: epochMs(performance.now()), value_ms: ms });
      }
    }

    ui.status("음성 연결 중");
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
        onUserPartial: (textSoFar) => {
          // 공백뿐인 조각은 건너뜁니다. 넘기면 "방금 보낸 답변"이 빈 칸으로 바뀝니다.
          if (textSoFar.trim()) showAnswer(textSoFar);
        },
        onAiTranscript,
        // 음성 AI가 도구 결과를 소리 없이 넘겼습니다. 3초를 기다리지 않고 바로 다시 보냅니다.
        onToolReplyEmpty: () => speakFallbackNow?.(),
        onError: (code, message) => {
          ui.log("error", message);
          metric("error", { error_code: code });
        },
        onClosed: (reason) => shutdown(`제공사 연결이 닫혔습니다 (${reason})`, "dropped"),
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
    ui.audio?.({ user: () => h.userAnalyser(), ai: () => h.aiAnalyser() });
    ui.status("연결됨");
    // 첫 발화도 say로 보내므로, 음성 AI가 이걸 답변처럼 넘기면 같은 방법으로 걸러 냅니다.
    resentUtterance = created.first.utterance;
    h.say(sayMessage(created.first.utterance));
  } catch (e) {
    // 시작 도중 실패하거나 멈추면, 열린 것을 모두 닫고 호출한 쪽에 알립니다.
    const reason = isAbort(e) ? "사용자가 멈췄습니다" : "시작하지 못했습니다";
    shutdown(reason, isAbort(e) ? "user" : "dropped");
    opts.signal.removeEventListener("abort", onOuterAbort);
    throw e;
  }

  return {
    stop() {
      opts.signal.removeEventListener("abort", onOuterAbort);
      shutdown("사용자가 멈췄습니다", "user");
    },
  };
}
