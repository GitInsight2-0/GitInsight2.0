"use client";

import type { Action, BrainReply, GazeSummary, LineSpan, SymbolRef, TurnRequest } from "@gitinsight/contracts";
import { useEffect, useRef, useState } from "react";
import CodeViewer from "@/components/CodeViewer";
import { interviewHref } from "@/lib/analyses";
import { reportHref } from "@/lib/reports";
import {
  ApiError,
  TRANSCRIPT_MAX,
  createSession,
  endSession,
  isAbort,
  isReportUnavailable,
  postTurn,
} from "@/lib/voice/api";
import { DEFAULT_VAD } from "@/lib/voice/config";
import { explainError, isLoginError } from "@/lib/voice/errors";
import { connectGemini } from "@/lib/voice/gemini";
import type { GazeZone } from "@/lib/gaze/classify";
import { CALIBRATION_MS, GazeError, GazeTracker } from "@/lib/gaze/tracker";
import { type AudioLevels, type SessionUi, type VoiceSession, startVoiceSession } from "@/lib/voice/session";
import { USER_SILENCE_MS } from "@/lib/voice/meter";
import styles from "./interview.module.css";
import Logo from "./Logo";
import Waveform from "./Waveform";

/**
 * 면접 화면 (T8, #30).
 * 음성 모드와 텍스트 모드를 오가며 같은 세션으로 면접을 이어 갑니다.
 * 음성 연결이 끊기거나 실패하면 텍스트 모드로 바꿔 이어 갑니다 (런북 "음성 제공사" 1차 대비책).
 *
 * 다음 질문, 반복, 힌트, 종료는 서버가 정하고 이 화면은 보여 주기만 합니다.
 */

interface HistoryItem {
  who: "interviewer" | "me" | "system";
  text: string;
  /** 면접관 말풍선 아래에 적을 질문 종류와 코드 위치 (서버가 준 값) */
  action?: Action;
  where?: SymbolRef | null;
  /** where 안에서 이 발화와 관계있는 줄 (#156). 없으면 where 전체입니다 */
  focus?: LineSpan | null;
}

/** 면접관 발화를 대화 기록 한 줄로 */
function said(r: BrainReply): HistoryItem {
  // 옛 서버는 focus를 보내지 않을 수 있어 null로 읽습니다.
  return { who: "interviewer", text: r.utterance, action: r.action, where: r.highlight, focus: r.focus ?? null };
}

type Mode = "text" | "voice";
type VoiceState = "idle" | "connecting" | "live";
/** 시선 분석 준비 단계. on이면 카메라가 켜져 있고 세션이 생기면 잽니다 */
type GazeStage = "off" | "preparing" | "calibrating" | "on";
/** 아래 막대와 말풍선이 보여 줄 상태 */
type Stage = "connecting" | "listening" | "muted" | "review" | "thinking" | "speaking" | "text" | "done";

const STAGE_LABEL: Record<Stage, string> = {
  connecting: "연결하고 있어요",
  listening: "듣고 있어요",
  muted: "마이크가 꺼져 있어요",
  review: "보내기 전에 답변을 확인해 주세요",
  thinking: "답변을 살펴보고 있어요",
  speaking: "말하는 중",
  text: "텍스트로 답하는 중",
  done: "면접이 끝났어요",
};

/**
 * 음성 답변을 보내기 전에 보여 주는 시간 (초, #148). 그대로 두면 이 시간 뒤에 보냅니다.
 * 답변마다 이만큼 늘고 서버의 남은 시간은 그대로 흘러서 짧게 둡니다 (#149 리뷰).
 */
const REVIEW_SEC = 3;
/**
 * 고치는 중에 이 시간 동안 아무것도 안 하면 고친 글로 보냅니다 (초, #149 리뷰).
 * 그동안 음성 AI가 도구 결과를 기다리므로 끝없이 붙잡아 두지 않습니다.
 */
const EDIT_IDLE_SEC = 60;

/** 서버로 보내기 전에 확인 중인 음성 답변 (#148). editing이면 카운트다운을 멈추고 글로 고치는 중입니다 */
interface Review {
  text: string;
  editing: boolean;
}

/** 말 끝 판정 침묵 (초). 안내 문구에 씁니다 */
const SILENCE_SEC = DEFAULT_VAD.silenceMs / 1000;
/**
 * 측정기가 "말이 멈췄다"고 본 뒤 이만큼 더 조용하면 "답변을 살펴보고 있어요"로 바꿉니다.
 * 측정기는 0.5초 침묵에서 멈춤으로 보고, 음성 AI는 2초 침묵에서 답변이 끝났다고 봅니다. 그 차이만큼 기다립니다.
 */
const THINKING_AFTER_MS = Math.max(0, DEFAULT_VAD.silenceMs - USER_SILENCE_MS);
/**
 * 생각 중으로 바꾼 뒤 이 시간 안에 턴이 서버로 가지 않으면 듣는 중으로 되돌립니다.
 * 기침 같은 짧은 잡음은 턴이 되지 않아서, 되돌리지 않으면 생각 중에 멈춰 보입니다.
 * 음성 AI는 보통 침묵 판정 뒤 1~2초 안에 턴을 넘깁니다.
 */
const THINKING_GIVE_UP_MS = 4000;

const ACTION_LABEL: Record<Action, string> = {
  ASK: "질문",
  FOLLOW_UP: "꼬리질문",
  PROBE: "확인 질문",
  HINT: "힌트",
  REPEAT: "다시 말씀드림",
  CLARIFY: "설명",
  NEXT_TOPIC: "다음 주제",
  WRAP_UP: "마무리",
  // 일부러 한 틀린 말이라는 것을 드러내면 답을 알려 주게 되어 PROBE와 같은 문구를 씁니다 (#133).
  MISSTATE: "확인 질문",
};

/** 리포트 요청 상태 */
type ReportState =
  | { kind: "idle" }
  | { kind: "making" }
  | { kind: "ready"; reportId: string }
  /** 리포트 서비스가 없는 서버 (LAB_ENABLED의 대본 두뇌) */
  | { kind: "unavailable" }
  | { kind: "error"; message: string };

/** 아무것도 하지 않는 측정 콜백 (측정은 /lab/voice에서 봅니다. 서버 /api/metrics에는 진행기가 그대로 보냅니다) */
const noop = () => undefined;

export default function InterviewScreen({
  analysisId,
  repo,
  reviewOnly = false,
}: {
  analysisId: string;
  repo: string | null;
  /** 다시 보기 면접: 지난 면접에서 답변이 부족했던 주제만 묻습니다 (#107). 어느 주제인지는 서버가 정합니다 */
  reviewOnly?: boolean;
}) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [reply, setReply] = useState<BrainReply | null>(null);
  /** 서버의 done 발화가 아니라 화면에서 끝냄. 이때 reply.utterance는 직전 질문이라 보여 주지 않습니다 (#84) */
  const [closedLocally, setClosedLocally] = useState(false);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 모드가 바뀐 이유처럼 사용자에게 알릴 문구 */
  const [notice, setNotice] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("text");
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  /** 음성 모드에서 방금 받아쓴 내 답변. 다음 질문이 오면 지웁니다 */
  const [lastAnswer, setLastAnswer] = useState<string | null>(null);
  /** lastAnswer가 이미 서버로 보낸 답변인지. 다음 질문이 와도 지우지 않고 "방금 보낸 답변"으로 남깁니다 */
  const [answerSent, setAnswerSentState] = useState(false);
  const answerSentRef = useRef(false);
  const setAnswerSent = (v: boolean) => {
    answerSentRef.current = v;
    setAnswerSentState(v);
  };
  /** 음성 턴을 서버로 보내고 답을 기다리는 중 */
  const [thinking, setThinking] = useState(false);
  const [aiSpeaking, setAiSpeaking] = useState(false);
  /** 타이머 안에서 AI가 말하는 중인지 보려고 상태와 같이 둡니다 */
  const aiSpeakingRef = useRef(false);
  const [levels, setLevels] = useState<AudioLevels | null>(null);
  /** 면접자가 음성 막대의 버튼으로 마이크를 껐는지 (#145). 음성을 새로 열면 켜진 상태로 돌아갑니다 */
  const [micOff, setMicOff] = useState(false);
  const [review, setReview] = useState<Review | null>(null);
  const [reviewDraft, setReviewDraft] = useState("");
  /** 확인이 끝나면 진행기에 보낼 답변(지웠으면 null)을 넘깁니다 */
  const reviewResolve = useRef<((text: string | null) => void) | null>(null);
  const reviewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 확인 중에 음성이 끝나면 입력칸으로 옮길 답변. 고치는 중이면 고친 글입니다 */
  const reviewText = useRef("");
  const [report, setReport] = useState<ReportState>({ kind: "idle" });
  /** 서버가 401을 돌려줬습니다. 첫 화면으로 가서 다시 로그인하라고 안내합니다 */
  const [needLogin, setNeedLogin] = useState(false);
  /** "끝내기"를 눌러 확인을 기다리는 중 */
  const [confirmEnd, setConfirmEnd] = useState(false);
  /** 이 세션의 리포트를 이미 요청했는지 (자동 요청이 두 번 나가지 않게) */
  const reportRequested = useRef(false);
  const reportAbort = useRef<AbortController | null>(null);
  const thinkingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** 실패한 턴. 같은 내용을 다시 보낼 때만 같은 turn_id를 씁니다 (고친 답변이 중복으로 버려지지 않게). */
  const pendingTurn = useRef<{ id: string; transcript: string } | null>(null);
  /** state인 busy는 연달아 누른 Enter에서 아직 false로 보일 수 있어, 보내는 중인지는 ref로 막습니다. */
  const sending = useRef(false);
  // 음성 진행기 콜백은 화면 state의 옛 값을 볼 수 있어, 최신 세션과 발화는 ref로도 들고 있습니다.
  const sessionRef = useRef<string | null>(null);
  const replyRef = useRef<BrainReply | null>(null);
  const voice = useRef<VoiceSession | null>(null);
  const voiceAbort = useRef<AbortController | null>(null);
  /** 사용자가 직접 음성을 끈 것인지 (끊김과 구분해 자동 전환 안내를 하지 않음) */
  const voiceStoppedByUser = useRef(false);

  /** 다시 보기 면접을 열었는데 다시 물을 질문이 없음 (서버 409, #118) */
  const [nothingToReview, setNothingToReview] = useState(false);
  /** 시선 분석 (#177). 면접자가 켰을 때만 카메라를 씁니다 */
  const [gazeWanted, setGazeWanted] = useState(false);
  const [gazeStage, setGazeStage] = useState<GazeStage>("off");
  const [gazeError, setGazeError] = useState<string | null>(null);
  const gaze = useRef<GazeTracker | null>(null);
  /** 끝낼 때 한 번 만든 요약. 리포트 요청을 다시 보내도 같은 값을 보냅니다 */
  const gazeSummary = useRef<GazeSummary | null>(null);

  /** 대화 말풍선 목록. 새 말풍선이 생기면 맨 아래로 내립니다 */
  const threadRef = useRef<HTMLDivElement | null>(null);

  /** 다시 보기 면접에서 서버가 "다시 볼 질문이 없다"(409)고 답했는지 */
  const isNothingToReview = (e: unknown) => reviewOnly && e instanceof ApiError && e.status === 409;

  const push = (item: HistoryItem) => setHistory((h) => [...h, item]);
  const showReply = (r: BrainReply) => {
    replyRef.current = r;
    setReply(r);
  };
  /** 서버가 done 발화를 보내지 않았는데 면접을 끝낼 때 (끝내기, 이미 끝난 세션 409) */
  const closeLocally = () => {
    const r = replyRef.current;
    if (!r || r.done) return;
    setClosedLocally(true);
    showReply({ ...r, done: true });
  };

  // 페이지를 떠나면 마이크와 음성 연결을 닫습니다. 서버 세션은 그대로 둡니다.
  useEffect(
    () => () => {
      voiceAbort.current?.abort();
      reportAbort.current?.abort();
      clearThinkingTimer();
      gaze.current?.close();
    },
    [],
  );

  // 세션이 생기면 시선 측정을 시작합니다. 보정은 시작 버튼을 누를 때 이미 끝났습니다.
  useEffect(() => {
    if (sessionId) gaze.current?.start();
  }, [sessionId]);

  // 면접이 끝나면(서버 done, 음성 종료, 409, 끝내기) 리포트를 요청합니다.
  // 음성으로 끝나면 AI가 마지막 인사를 하는 동안 리포트가 같이 만들어집니다.
  const finished = reply?.done === true;
  useEffect(() => {
    if (finished) void requestReport();
    // requestReport는 ref로 한 번만 보내므로 finished만 봅니다.
  }, [finished]);

  // 새 말풍선(질문, 받아쓰기, 기다림, 리포트 안내)이 생기면 대화 칸을 맨 아래로 내립니다. 페이지 전체는 움직이지 않습니다.
  useEffect(() => {
    const box = threadRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [history.length, lastAnswer, thinking, busy, aiSpeaking]);

  async function requestReport() {
    const id = sessionRef.current;
    if (!id || reportRequested.current) return;
    reportRequested.current = true;
    const ac = new AbortController();
    reportAbort.current = ac;
    setReport({ kind: "making" });
    // 면접이 끝나면 카메라를 끄고 시선 요약을 한 번만 만듭니다.
    if (gaze.current) {
      gazeSummary.current = gaze.current.finish();
      gaze.current = null;
      setGazeStage("off");
    }
    try {
      const ended = await endSession(id, ac.signal, gazeSummary.current);
      setReport({ kind: "ready", reportId: ended.report_id });
    } catch (e) {
      if (ac.signal.aborted) return;
      // 리포트를 만들지 않는 서버일 때만 안내하고 끝냅니다. DB 장애 같은 다른 503은 아래에서 "다시 시도"를 보여 줍니다.
      if (isReportUnavailable(e)) {
        setReport({ kind: "unavailable" });
        return;
      }
      // 서버는 같은 세션에 같은 report_id를 돌려주므로 다시 시도해도 됩니다.
      reportRequested.current = false;
      if (isLoginError(e)) setNeedLogin(true);
      const message = isAbort(e) ? "시간이 너무 오래 걸립니다" : explainError(e);
      setReport({ kind: "error", message });
    }
  }

  /** 확인을 받은 뒤 면접을 끝냅니다. 음성이 켜져 있으면 끄고, 화면을 종료 상태로 바꿉니다 (리포트는 위 effect가 요청) */
  function endInterview() {
    setConfirmEnd(false);
    if (voice.current || voiceState !== "idle") {
      voiceStoppedByUser.current = true;
      voiceAbort.current?.abort();
      voice.current?.stop();
      voice.current = null;
      resetVoiceView();
      setVoiceState("idle");
      setMode("text");
    }
    push({ who: "system", text: "면접을 끝냈습니다." });
    closeLocally();
  }

  function clearThinkingTimer() {
    if (thinkingTimer.current) clearTimeout(thinkingTimer.current);
    thinkingTimer.current = null;
  }

  // ── 텍스트 ──

  /**
   * 시선 분석을 켰으면 카메라를 열고 3초 동안 카메라를 보게 해 기준을 잡습니다.
   * 실패하면 면접을 시작하지 않고 이유를 보여 줍니다. 체크를 끄면 시선 분석 없이 시작할 수 있습니다.
   */
  async function prepareGaze(): Promise<boolean> {
    if (!gazeWanted || gaze.current) return true;
    setGazeError(null);
    setGazeStage("preparing");
    let tracker: GazeTracker;
    try {
      tracker = await GazeTracker.open();
    } catch (e) {
      setGazeStage("off");
      setGazeError(e instanceof GazeError ? e.message : "카메라를 켜지 못했어요.");
      return false;
    }
    gaze.current = tracker;
    setGazeStage("calibrating");
    const ok = await tracker.calibrate();
    if (!ok) {
      tracker.close();
      gaze.current = null;
      setGazeStage("off");
      setGazeError("얼굴이 잘 보이지 않아요. 밝은 곳에서 얼굴이 화면 가운데 오게 한 뒤 다시 눌러 주세요.");
      return false;
    }
    setGazeStage("on");
    return true;
  }

  async function begin(kind: Mode) {
    if (busy || gazeStage === "preparing" || gazeStage === "calibrating") return;
    if (!(await prepareGaze())) return;
    if (kind === "voice") await startVoice();
    else await startText();
    // 시작에 실패해 시작 화면에 남았으면(로그인 만료, 다시 볼 질문 없음, 음성 연결 실패) 카메라를 끕니다.
    // 시작 화면에는 "끄기" 칩이 없어서 켜 두면 끌 방법이 없습니다. 다시 누르면 보정부터 다시 합니다.
    if (!sessionRef.current && !voice.current) closeGaze();
  }

  /** 카메라를 끄고 그때까지 잰 값을 버립니다 */
  function closeGaze() {
    gaze.current?.close();
    gaze.current = null;
    setGazeStage("off");
  }

  /** 면접 도중 시선 분석을 끕니다. 그때까지 잰 값도 버리고 리포트에 시선 칸을 넣지 않습니다 */
  function stopGaze() {
    closeGaze();
    setNotice("시선 분석을 껐어요. 이번 리포트에는 시선 결과가 나오지 않아요.");
  }

  async function startText() {
    setBusy(true);
    setError(null);
    try {
      const created = await createSession({ analysis_id: analysisId, mode: "text", review_only: reviewOnly });
      sessionRef.current = created.session_id;
      setSessionId(created.session_id);
      showReply(created.first);
      setHistory([said(created.first)]);
    } catch (e) {
      if (isNothingToReview(e)) setNothingToReview(true);
      else {
        if (isLoginError(e)) setNeedLogin(true);
        setError(explainError(e));
      }
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    const transcript = draft.trim();
    if (!sessionId || !transcript || sending.current || reply?.done || mode !== "text") return;
    sending.current = true;
    setBusy(true);
    setError(null);
    // 실패 뒤 같은 답변을 그대로 다시 보내면 같은 turn_id를 써서 서버가 한 번만 반영하게 합니다.
    // 답변을 고쳤으면 새 턴이므로 새 turn_id를 씁니다.
    const pending = pendingTurn.current;
    const turnId = pending && pending.transcript === transcript ? pending.id : crypto.randomUUID();
    pendingTurn.current = { id: turnId, transcript };
    try {
      const next = await postTurn(sessionId, {
        turn_id: turnId,
        transcript,
        source: "text",
        speech_started_at: null,
        speech_ended_at: null,
      });
      pendingTurn.current = null;
      push({ who: "me", text: transcript });
      push(said(next));
      showReply(next);
      setDraft("");
    } catch (e) {
      // 서버(api/routes/sessions.py)는 /turns에서 409를 "이미 끝난 세션"에만 씁니다.
      if (e instanceof ApiError && e.status === 409) {
        pendingTurn.current = null;
        push({ who: "system", text: "이미 끝난 면접입니다." });
        closeLocally();
      } else if (isLoginError(e)) {
        setNeedLogin(true);
        setError(explainError(e));
      } else {
        setError(`답변을 보내지 못했습니다. 다시 보내 주세요. (${explainError(e)})`);
      }
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  /**
   * 음성 턴의 응답을 받기 전에 음성이 끝났을 때, 같은 turn_id로 다시 보내 화면 질문을 서버와 맞춥니다 (#47).
   * 서버는 이미 처리한 turn_id면 저장된 응답을, 처리 중이면 끝날 때까지 기다렸다 같은 응답을 돌려줍니다.
   * 그래도 못 받으면 그 답변을 입력칸에 넣어 둡니다. 그대로 보내면 같은 turn_id로 가서 서버가 한 번만 반영합니다.
   */
  async function resyncTurn(turn: TurnRequest) {
    const id = sessionRef.current;
    if (!id || sending.current) return;
    sending.current = true;
    setBusy(true);
    setError(null);
    pendingTurn.current = { id: turn.turn_id, transcript: turn.transcript };
    try {
      const next = await postTurn(id, turn);
      pendingTurn.current = null;
      // 기다리는 사이 "끝내기"로 면접을 끝냈으면 종료 화면을 덮지 않습니다.
      if (replyRef.current?.done) return;
      push(said(next));
      showReply(next);
    } catch (e) {
      if (replyRef.current?.done) {
        pendingTurn.current = null;
        return;
      }
      if (e instanceof ApiError && e.status === 409) {
        pendingTurn.current = null;
        push({ who: "system", text: "이미 끝난 면접입니다." });
        closeLocally();
      } else if (isLoginError(e)) {
        setNeedLogin(true);
        setError(explainError(e));
      } else {
        setDraft(turn.transcript);
        setError(`음성으로 보낸 마지막 답변의 결과를 받지 못했어요. 입력칸의 답변을 그대로 보내 주세요. (${explainError(e)})`);
      }
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  // ── 음성 ──

  /** 음성 면접을 시작합니다. 진행 중인 세션이 있으면 현재 질문부터 음성으로 이어 갑니다. */
  async function startVoice() {
    if (voice.current || voiceState !== "idle") return;
    const existing =
      sessionRef.current && replyRef.current && !replyRef.current.done
        ? { sessionId: sessionRef.current, current: replyRef.current }
        : undefined;
    const ac = new AbortController();
    voiceAbort.current = ac;
    voiceStoppedByUser.current = false;
    // 실패했던 텍스트 턴의 turn_id를 음성 뒤에 다시 쓰면, 서버가 몇 턴 전 응답을 돌려줄 수 있습니다.
    pendingTurn.current = null;
    /** 이 시도의 콜백인지. 끄고 바로 다시 켜면 이전 시도의 늦은 콜백이 새 시도의 상태를 덮지 않게 합니다 */
    const current = () => voiceAbort.current === ac;
    setError(null);
    setNotice(null);
    setMode("voice");
    setVoiceState("connecting");

    const ui: SessionUi = {
      // 대화 기록에는 서버로 실제로 보낸 답변(turnSent)과 서버가 정한 문장(reply)만 남깁니다.
      log: noop,
      answer: (textSoFar) => {
        // 진행기가 이번 턴 받아쓰기 전체를 말하는 동안 계속 보내 줍니다. 늦은 조각은 진행기가 걸러 줍니다.
        if (!current()) return;
        setLastAnswer(textSoFar);
        setAnswerSent(false);
      },
      status: (s) => {
        if (s === "연결됨") setVoiceState("live");
      },
      started: (id, current) => {
        if (!sessionRef.current) {
          sessionRef.current = id;
          setSessionId(id);
          setHistory([said(current)]);
          showReply(current);
        }
      },
      reply: (r) => {
        // 이어받은 세션의 현재 발화는 이미 기록에 있으므로 다시 넣지 않습니다.
        if (r === replyRef.current) return;
        push(said(r));
        showReply(r);
        // "답변을 살펴보고 있어요"는 AI가 실제로 말하기 시작할 때 끕니다 (speaking).
        // 방금 보낸 답변은 다음 답변을 말하기 시작할 때까지 남겨 둡니다.
        // Gemini가 받아쓰기를 말이 끝난 뒤에 한꺼번에 주면, 지우는 순간 사용자가 한 번도 못 보기 때문입니다.
      },
      speaking: (who, on) => {
        if (!current()) return;
        clearThinkingTimer();
        if (who === "ai") {
          aiSpeakingRef.current = on;
          setAiSpeaking(on);
          // AI가 말하기 시작하면 기다림은 끝난 것입니다 (서버 응답 실패 때 나오는 다시 말해 달라는 문장 포함).
          if (on) setThinking(false);
          return;
        }
        if (on) {
          // 다시 말하기 시작했으면 아직 답변 중입니다. 지난 턴에 보낸 답변은 비우고 새로 받아 적습니다.
          setThinking(false);
          if (answerSentRef.current) {
            setLastAnswer(null);
            setAnswerSent(false);
          }
          return;
        }
        // 말을 멈췄습니다. 음성 AI가 답변 끝으로 볼 만큼 조용하면 기다리는 중으로 표시합니다.
        thinkingTimer.current = setTimeout(() => {
          // AI가 말하는 중이면 사용자 소리는 맞장구나 반향입니다. 생각 중으로 바꾸지 않습니다.
          if (aiSpeakingRef.current) return;
          setThinking(true);
          thinkingTimer.current = setTimeout(() => setThinking(false), THINKING_GIVE_UP_MS);
        }, THINKING_AFTER_MS);
      },
      audio: (levels) => {
        if (current()) setLevels(levels);
      },
      ended: (reason, kind, unfinished) => {
        if (!current()) return;
        voice.current = null;
        resetVoiceView();
        setVoiceState("idle");
        setMode("text");
        if (kind === "done") {
          // 서버가 409(이미 끝난 세션)로 답하면 done인 reply가 오지 않습니다. 화면도 종료로 맞춥니다.
          closeLocally();
          return;
        }
        if (replyRef.current?.done) return;
        if (!voiceStoppedByUser.current && sessionRef.current) {
          setNotice(`음성 연결이 끊겨 텍스트로 이어 갑니다. 다시 시도하려면 위의 "음성"을 누르세요. (${reason})`);
        }
        // 서버로 간 답변의 응답을 못 받았습니다. 화면 질문이 서버보다 한 단계 늦지 않게 맞춥니다 (#47).
        if (unfinished) void resyncTurn(unfinished);
      },
      review: (transcript, signal) =>
        new Promise<string | null>((resolve) => {
          if (!current() || signal.aborted) {
            resolve(null);
            return;
          }
          clearThinkingTimer();
          setThinking(false);
          reviewResolve.current = resolve;
          reviewText.current = transcript;
          setReviewDraft(transcript);
          setReview({ text: transcript, editing: false });
          reviewTimer.current = setTimeout(() => finishReview(transcript), REVIEW_SEC * 1000);
          signal.addEventListener(
            "abort",
            () => {
              if (reviewResolve.current !== resolve) return;
              // 보내지 못한 답변을 텍스트 입력칸으로 옮겨 다시 말하지 않아도 되게 합니다.
              const left = reviewText.current.trim();
              if (left) setDraft((d) => (d.trim() ? d : left));
              finishReview(null);
            },
            { once: true },
          );
        }),
      latency: noop,
      filler: noop,
      delegation: noop,
      bargeIn: noop,
      bargeStop: noop,
      transcriptSimilarity: noop,
      delegationMissed: noop,
      turnSent: (transcript) => {
        if (!current()) return;
        push({ who: "me", text: transcript });
        // 서버로 보낸 답변 전체를 다음 답변을 시작할 때까지 보여 줍니다.
        setLastAnswer(transcript);
        setAnswerSent(true);
        // 턴이 실제로 서버로 갔습니다. AI가 말을 시작할 때까지 생각 중을 유지합니다.
        clearThinkingTimer();
        setThinking(true);
      },
    };

    try {
      voice.current = await startVoiceSession(connectGemini, ui, {
        analysisId,
        reviewOnly,
        labDelayMs: 0,
        scenario: () => "interview",
        vad: DEFAULT_VAD,
        filler: false,
        signal: ac.signal,
        existing,
      });
    } catch (e) {
      // 진행기가 이미 ended로 텍스트 모드 전환을 처리했습니다. 세션을 못 만든 경우만 여기서 알립니다.
      if (!current()) return;
      voice.current = null;
      resetVoiceView();
      setVoiceState("idle");
      setMode("text");
      if (voiceStoppedByUser.current || isAbort(e)) return;
      if (!sessionRef.current && isNothingToReview(e)) {
        setNothingToReview(true);
        return;
      }
      if (isLoginError(e)) setNeedLogin(true);
      const message = explainError(e);
      if (sessionRef.current) setNotice(`음성으로 연결하지 못해 텍스트로 이어 갑니다. (${message})`);
      else setError(message);
    }
  }

  /** 음성을 끄고 같은 세션으로 텍스트 면접을 이어 갑니다. */
  function switchToText() {
    voiceStoppedByUser.current = true;
    voiceAbort.current?.abort();
    voice.current?.stop();
    voice.current = null;
    resetVoiceView();
    setVoiceState("idle");
    setMode("text");
    push({ who: "system", text: "텍스트로 바꿨습니다." });
  }

  function resetVoiceView() {
    clearThinkingTimer();
    setLevels(null);
    setThinking(false);
    aiSpeakingRef.current = false;
    setAiSpeaking(false);
    setLastAnswer(null);
    setAnswerSent(false);
    setMicOff(false);
  }

  /** 음성 답변 확인을 끝냅니다. text가 null이면 지운 것입니다 (#148) */
  function finishReview(text: string | null) {
    if (reviewTimer.current) clearTimeout(reviewTimer.current);
    reviewTimer.current = null;
    const resolve = reviewResolve.current;
    reviewResolve.current = null;
    reviewText.current = "";
    setReview(null);
    resolve?.(text);
  }

  function editReview() {
    setReview((r) => (r ? { ...r, editing: true } : r));
    armEditIdle();
  }

  /** 고치는 중 쉬는 시간을 다시 잽니다. 글을 고칠 때마다 다시 시작합니다 */
  function armEditIdle() {
    if (reviewTimer.current) clearTimeout(reviewTimer.current);
    reviewTimer.current = setTimeout(() => {
      const text = reviewText.current.trim();
      if (text) finishReview(text);
      else discardReview();
    }, EDIT_IDLE_SEC * 1000);
  }

  function discardReview() {
    finishReview(null);
    setLastAnswer(null);
    setAnswerSent(false);
    push({ who: "system", text: "답변을 지웠어요. 같은 질문에 다시 답해 주세요." });
  }

  function sendReview() {
    if (!review) return;
    const text = review.editing ? reviewDraft.trim() : review.text;
    if (!text) return;
    finishReview(text);
  }

  /** 마이크를 끄거나 켭니다. 말하던 중에 끄면 그때까지 한 말이 답변으로 넘어갑니다 */
  function toggleMic() {
    const next = !micOff;
    voice.current?.setMuted(next);
    setMicOff(next);
  }

  // ── 화면 ──

  if (!sessionId && voiceState === "idle") {
    return (
      <div className={styles.page}>
        <header className={styles.topbar}>
          <a className={styles.brand} href="/">
            <Logo size={22} />
            GitInsight
          </a>
        </header>
        <main className={styles.introWrap}>
          <section className={styles.intro}>
            <Logo size={52} className={styles.introMark} />
            <h1>{reviewOnly ? "부족했던 질문 다시 보기" : "모의 기술면접"}</h1>
            {nothingToReview ? (
              <>
                <p>다시 볼 질문이 없어요. 이 레포로 본 면접에서 막혔거나 힌트를 받고 답한 질문이 남아 있지 않아요.</p>
                <div className={styles.introActions}>
                  <a className={styles.primary} href={interviewHref(analysisId, repo ?? undefined)}>
                    일반 면접으로 시작
                  </a>
                  <a className={styles.secondary} href="/">
                    첫 화면으로
                  </a>
                </div>
              </>
            ) : (
              <>
                <p>
                  {reviewOnly
                    ? "지난 면접에서 막혔거나 힌트를 받고 답한 질문만 다시 묻습니다. 음성으로 답하거나 텍스트로 답할 수 있고, 도중에 바꿀 수 있습니다."
                    : "레포 분석 결과를 바탕으로 질문합니다. 음성으로 답하거나 텍스트로 답할 수 있고, 도중에 바꿀 수 있습니다."}
                </p>
                <GazeOption
                  wanted={gazeWanted}
                  onChange={(v) => {
                    setGazeWanted(v);
                    setGazeError(null);
                    // 체크를 풀면 열어 둔 카메라도 닫아, 다시 시작했을 때 꺼 둔 측정이 이어지지 않게 합니다.
                    if (!v) closeGaze();
                  }}
                  stage={gazeStage}
                  stream={gaze.current?.stream ?? null}
                  error={gazeError}
                />
                <div className={styles.introActions}>
                  <button
                    type="button"
                    className={styles.primary}
                    onClick={() => void begin("voice")}
                    disabled={busy || gazeStage === "preparing" || gazeStage === "calibrating"}
                  >
                    음성으로 시작
                  </button>
                  <button
                    type="button"
                    className={styles.secondary}
                    onClick={() => void begin("text")}
                    disabled={busy || gazeStage === "preparing" || gazeStage === "calibrating"}
                  >
                    텍스트로 시작
                  </button>
                </div>
                <p className={styles.note}>
                  <MicIcon />
                  음성은 Chrome에서 이어폰을 끼고 쓰세요. 스피커로 들으면 AI가 자기 목소리를 듣고 끊길 수 있습니다.
                </p>
                {/* 언제, 어느 질문에서 하는지는 알리지 않습니다 (#133). 다시 보기 면접에서는 하지 않습니다. */}
                {!reviewOnly && (
                  <p className={styles.note}>면접관이 확인을 위해 일부러 틀린 말을 할 수 있습니다.</p>
                )}
              </>
            )}
            <p role="status" aria-live="polite" className={styles.error}>
              {error}
            </p>
            {needLogin && <LoginLink />}
          </section>
        </main>
      </div>
    );
  }

  const done = reply?.done === true;
  const stage: Stage = done
    ? "done"
    : mode === "text"
      ? busy
        ? "thinking"
        : "text"
      : voiceState !== "live"
        ? "connecting"
        : review
          ? "review"
          : thinking
            ? "thinking"
            : aiSpeaking
            ? "speaking"
            : micOff
              ? "muted"
              : "listening";
  const canSwitchToVoice = mode === "text" && voiceState === "idle" && !busy && !done;
  // 음성으로 말하는 중인 답변. 아직 서버로 보내지 않아서 점선 말풍선에 받아쓰기를 보여 줍니다.
  const live = mode === "voice" && !done && !answerSent && !review && (lastAnswer !== null || stage === "listening");
  // 텍스트로 보내는 중인 답변. 서버가 받으면 기록에 들어갑니다.
  const sendingText = mode === "text" && busy && draft.trim() ? draft.trim() : null;
  const lastInterviewer = history.reduce((at, h, i) => (h.who === "interviewer" ? i : at), -1);

  // 면접이 끝나면 대화 칸과 코드 뷰어를 치우고 리포트 준비 화면만 보여 줍니다 (#132).
  // 음성으로 끝났으면 면접관의 마지막 인사는 이 화면에서도 끝까지 들립니다.
  if (done) {
    return (
      <div className={styles.page}>
        <header className={styles.topbar}>
          <a className={styles.brand} href="/">
            <Logo size={22} />
            GitInsight
          </a>
        </header>
        <main className={styles.introWrap}>
          <EndScreen
            report={report}
            answers={history.filter((h) => h.who === "me").length}
            history={history}
            href={reportHref}
            onRetry={() => void requestReport()}
            needLogin={needLogin}
          />
        </main>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <header className={styles.topbar}>
        <a className={styles.brand} href="/">
          <Logo size={22} />
          GitInsight
        </a>
        {reply && !closedLocally && !done && <span className={styles.chip}>{ACTION_LABEL[reply.action]}</span>}
        <span className={styles.spacer} />
        {reply && !done && (
          <span className={styles.time}>
            남은 시간 <b>{remainingLabel(reply.remaining_sec)}</b>
          </span>
        )}
        {gazeStage === "on" && !done && gaze.current && <GazeChip tracker={gaze.current} onStop={stopGaze} />}
        {reply && !done && (
          <button type="button" className={styles.end} onClick={() => setConfirmEnd(true)} disabled={busy || confirmEnd}>
            끝내기
          </button>
        )}
      </header>

      <main className={styles.layout}>
        <section aria-label="면접 대화" className={styles.chat}>
          {notice && (
            <p role="status" aria-live="polite" className={styles.notice}>
              {notice}
            </p>
          )}

          {needLogin && (
            <div className={styles.notice}>
              로그인이 풀렸어요. 로그인은 8시간 동안 유지돼요. <LoginLink />
            </div>
          )}

          {confirmEnd && !done && (
            <div role="group" aria-label="면접 끝내기 확인" className={styles.confirm}>
              <p>면접을 끝내고 리포트를 만들까요? 끝내면 이어서 답할 수 없어요.</p>
              <div className={styles.confirmActions}>
                <button type="button" className={styles.secondary} onClick={() => setConfirmEnd(false)} autoFocus>
                  계속하기
                </button>
                <button type="button" className={styles.primary} onClick={endInterview}>
                  끝내고 리포트 만들기
                </button>
              </div>
            </div>
          )}

          <div ref={threadRef} className={styles.thread} role="log" aria-live="polite" aria-label="대화 기록">
            {history.length === 0 && <p className={styles.system}>{STAGE_LABEL[stage]}</p>}
            {history.map((h, i) =>
              h.who === "system" ? (
                <p key={i} className={styles.system}>
                  {h.text}
                </p>
              ) : h.who === "interviewer" ? (
                <div key={i} className={`${styles.msg} ${styles.ai}`}>
                  <span className={styles.who}>
                    면접관
                    {h.action && <span className={styles.tag}>{ACTION_LABEL[h.action]}</span>}
                    {i === lastInterviewer && stage === "speaking" && <span className={styles.speaking}>말하는 중</span>}
                  </span>
                  <p className={styles.bubble}>{h.text}</p>
                  {h.where && (
                    <span className={styles.ref}>
                      {h.where.path}{" "}
                      <b>
                        {(h.focus ?? h.where).start_line}–{(h.focus ?? h.where).end_line}줄
                      </b>
                    </span>
                  )}
                </div>
              ) : (
                <div key={i} className={`${styles.msg} ${styles.me}`}>
                  <span className={styles.who}>나</span>
                  <p className={styles.bubble}>{h.text}</p>
                  <span className={styles.sent}>보냄</span>
                </div>
              ),
            )}

            {sendingText && (
              <div className={`${styles.msg} ${styles.me} ${styles.pending}`}>
                <span className={styles.who}>나</span>
                <p className={styles.bubble}>{sendingText}</p>
                <span className={styles.sent}>보내는 중</span>
              </div>
            )}

            {live && (
              <div className={`${styles.msg} ${styles.me} ${styles.live}`}>
                <span className={styles.who}>나 · 듣는 중</span>
                <div className={styles.bubble}>
                  <Waveform levels={levels} active={stage === "listening"} maxHeight={26} />
                  <p className={styles.partial}>{lastAnswer ?? "말씀하시면 여기에 받아 적어요."}</p>
                </div>
              </div>
            )}

            {review && mode === "voice" && !done && (
              <div className={`${styles.msg} ${styles.me} ${styles.review}`} role="group" aria-label="보내기 전 답변 확인">
                <span className={styles.who}>
                  나 · {review.editing ? `고치는 중 (${EDIT_IDLE_SEC}초 동안 그대로 두면 보내요)` : `${REVIEW_SEC}초 뒤 보내요`}
                </span>
                <div className={styles.bubble}>
                  {review.editing ? (
                    <textarea
                      className={styles.reviewEdit}
                      value={reviewDraft}
                      onChange={(e) => {
                        setReviewDraft(e.target.value);
                        reviewText.current = e.target.value;
                        armEditIdle();
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                          e.preventDefault();
                          sendReview();
                        }
                      }}
                      aria-label="답변 고치기"
                      maxLength={TRANSCRIPT_MAX}
                      rows={3}
                      autoFocus
                    />
                  ) : (
                    <>
                      <p className={styles.reviewText}>{review.text}</p>
                      <span className={styles.countdown} style={{ animationDuration: `${REVIEW_SEC}s` }} aria-hidden="true" />
                    </>
                  )}
                  <div className={styles.reviewActions}>
                    <button
                      type="button"
                      className={styles.send}
                      onClick={sendReview}
                      disabled={review.editing && !reviewDraft.trim()}
                    >
                      {review.editing ? "보내기" : "지금 보내기"}
                    </button>
                    {!review.editing && (
                      <button type="button" className={styles.reviewButton} onClick={editReview}>
                        고치기
                      </button>
                    )}
                    <button type="button" className={styles.reviewButton} onClick={discardReview}>
                      지우기
                    </button>
                  </div>
                </div>
              </div>
            )}

            {stage === "thinking" && (
              <div className={`${styles.msg} ${styles.ai} ${styles.waiting}`}>
                <span className={styles.who}>면접관</span>
                <p className={styles.bubble}>
                  <span className={styles.dots} aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </span>
                  답변을 살펴보고 있어요
                </p>
              </div>
            )}

          </div>

          {!done && (
            <footer className={styles.dock}>
              {mode === "voice" ? (
                <div className={styles.mic}>
                  <button
                    type="button"
                    className={micOff ? `${styles.micButton} ${styles.micButtonOff}` : styles.micButton}
                    onClick={toggleMic}
                    disabled={voiceState !== "live" || review !== null}
                    // 이름은 그대로 두고 눌림 상태로 켜짐·꺼짐을 알립니다. 화면 낭독기가 "마이크 끄기, 눌림"처럼 읽습니다.
                    aria-label="마이크 끄기"
                    aria-pressed={micOff}
                    title={micOff ? "마이크 켜기" : "마이크 끄기"}
                  >
                    {micOff ? <MicOffIcon /> : <MicIcon />}
                  </button>
                  <Waveform levels={levels} active={stage === "listening" || stage === "speaking"} maxHeight={22} />
                  <div>
                    <div role="status" aria-live="polite" className={styles.micState}>
                      {STAGE_LABEL[stage]}
                    </div>
                    <div className={styles.hint}>
                      {review
                        ? "그대로 두면 보내요. 잘못 들렸으면 고치거나 지우세요"
                        : micOff
                        ? "답할 때 마이크 버튼을 눌러 켜세요. 꺼진 동안의 소리는 보내지 않아요"
                        : `말을 마치고 ${SILENCE_SEC}초 쉬면 답변이 전송돼요. 다 말했으면 마이크를 꺼도 바로 보내요`}
                    </div>
                  </div>
                </div>
              ) : (
                <div className={styles.composer}>
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      // 한글 입력 중(조합 중)에는 Enter로 보내지 않습니다.
                      if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                        e.preventDefault();
                        void submit();
                      }
                    }}
                    aria-label="답변"
                    maxLength={TRANSCRIPT_MAX}
                    placeholder="답변을 입력하세요 (Enter 보내기, Shift+Enter 줄바꿈)"
                    rows={2}
                    disabled={busy}
                  />
                  <button type="button" className={styles.send} onClick={() => void submit()} disabled={busy || !draft.trim()}>
                    {busy ? "보내는 중…" : "보내기"}
                  </button>
                </div>
              )}
              <div role="group" aria-label="답변 방식" className={styles.toggle}>
                <button
                  type="button"
                  aria-pressed={mode === "voice"}
                  onClick={() => void startVoice()}
                  disabled={mode === "voice" || !canSwitchToVoice}
                >
                  음성
                </button>
                <button type="button" aria-pressed={mode === "text"} onClick={switchToText} disabled={mode === "text"}>
                  텍스트
                </button>
              </div>
              {error && (
                <p role="status" aria-live="polite" className={styles.barError}>
                  {error}
                </p>
              )}
            </footer>
          )}
        </section>

        <CodeViewer
          analysisId={analysisId}
          repo={repo}
          target={reply?.highlight ?? null}
          focus={reply?.highlight ? (reply.focus ?? null) : null}
        />
      </main>
    </div>
  );
}

/**
 * 시작 화면의 시선 분석 선택 (#177). 기본은 꺼짐입니다.
 * 보정하는 동안에만 내 얼굴을 작게 보여 줍니다.
 */
function GazeOption({
  wanted,
  onChange,
  stage,
  stream,
  error,
}: {
  wanted: boolean;
  onChange: (v: boolean) => void;
  stage: GazeStage;
  stream: MediaStream | null;
  error: string | null;
}) {
  const busy = stage === "preparing" || stage === "calibrating";
  return (
    <div className={styles.gazeOption}>
      <label className={styles.gazeToggle}>
        <input type="checkbox" checked={wanted} onChange={(e) => onChange(e.target.checked)} disabled={busy} />
        <span>
          <b>시선 분석 켜기 (선택)</b>
          <span className={styles.gazeHelp}>
            웹캠으로 면접관(카메라)을 얼마나 바라봤는지 재서 리포트에 참고로 보여 드려요. 영상은 이 브라우저 안에서만 처리하고
            서버로 보내거나 저장하지 않아요. 서버에는 비율 같은 요약 숫자만 가고, 역량 수준에는 반영하지 않아요.
          </span>
          <span className={styles.gazeHelp}>
            화상 면접 연습이라면 음성으로 답할 때 쓰세요. 텍스트로 답하면 키보드를 보는 시간이 화면 밖으로 잡힐 수 있어요.
          </span>
        </span>
      </label>
      {busy && (
        <div className={styles.gazeCalib} role="status" aria-live="polite">
          {stream && <CameraPreview stream={stream} />}
          <p>
            {stage === "preparing"
              ? "카메라와 시선 분석 도구를 준비하고 있어요…"
              : `카메라 렌즈를 ${CALIBRATION_MS / 1000}초 동안 바라봐 주세요.`}
          </p>
        </div>
      )}
      {error && (
        <p role="alert" className={styles.gazeError}>
          {error}
        </p>
      )}
    </div>
  );
}

const ZONE_LABEL: Record<GazeZone, string> = {
  camera: "카메라",
  screen: "화면",
  away: "화면 밖",
  none: "얼굴 없음",
};

/**
 * 면접 중 "시선 측정 중 · 끄기" 표시.
 * 주소에 ?gaze_debug=1을 붙이면 지금 장면을 어디로 나눴는지도 보여 줘서, 실제 웹캠으로 판정 기준을 맞춰 볼 수 있습니다.
 */
function GazeChip({ tracker, onStop }: { tracker: GazeTracker; onStop: () => void }) {
  const [debug] = useState(() => {
    try {
      return new URLSearchParams(window.location.search).get("gaze_debug") === "1";
    } catch {
      return false;
    }
  });
  const [zone, setZone] = useState<GazeZone | null>(null);
  useEffect(() => {
    if (!debug) return;
    const t = setInterval(() => setZone(tracker.zone), 250);
    return () => clearInterval(t);
  }, [debug, tracker]);
  return (
    <span className={styles.gazeChip}>
      <span className={styles.gazeDot} aria-hidden="true" />
      <span className={styles.gazeChipText}>시선 측정 중</span>
      {debug && <b>{zone ? ZONE_LABEL[zone] : "…"}</b>}
      <button type="button" onClick={onStop} aria-label="시선 분석 끄기">
        끄기
      </button>
    </span>
  );
}

function CameraPreview({ stream }: { stream: MediaStream }) {
  const ref = useRef<HTMLVideoElement | null>(null);
  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    video.srcObject = stream;
    void video.play().catch(() => undefined);
    return () => {
      video.srcObject = null;
    };
  }, [stream]);
  return <video ref={ref} className={styles.gazePreview} muted playsInline aria-label="내 카메라 화면" />;
}

/** 첫 화면(로그인 버튼이 있는 곳)으로 가는 링크 */
function LoginLink() {
  return (
    <a className={styles.textLink} href="/">
      첫 화면에서 다시 로그인
    </a>
  );
}

/** 면접이 끝난 뒤 리포트가 어디까지 됐는지 보여 줍니다 */
/**
 * 면접이 끝난 뒤 리포트를 기다리는 화면 (#132).
 * 서버는 리포트 진행률을 주지 않아서, 지난 시간과 하는 일을 보여 줘 멈춘 게 아니라는 것을 알립니다.
 */
function EndScreen({
  report,
  answers,
  history,
  href,
  onRetry,
  needLogin,
}: {
  report: ReportState;
  /** 서버로 보낸 답변 수 */
  answers: number;
  history: HistoryItem[];
  /** 리포트 화면 주소 (/reports/{id}) */
  href: (reportId: string) => string;
  onRetry: () => void;
  needLogin: boolean;
}) {
  const making = report.kind === "idle" || report.kind === "making";
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!making) return;
    const t0 = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [making]);

  return (
    <section className={`${styles.intro} ${styles.endCard}`} aria-labelledby="end-title">
      <Logo size={52} className={styles.introMark} />
      <h1 id="end-title">면접이 끝났어요</h1>
      <p>수고하셨어요. {answers > 0 ? `답변 ${answers}개를 바탕으로 리포트를 만들어요.` : "리포트를 만들어요."}</p>

      <div role="status" aria-live="polite" className={styles.endStatus}>
        {making && (
          <>
            <div className={styles.endRow}>
              <span className={styles.spinner} aria-hidden="true" />
              <b>리포트를 만들고 있어요</b>
              <span className={styles.elapsed}>{elapsed}초</span>
            </div>
            <div className={styles.endTrack} aria-hidden="true">
              <span />
            </div>
            <p className={styles.reportNote}>
              답변을 다시 읽고 역량별 수준과 근거가 된 말을 정리하고 있어요. 길면 1분쯤 걸려요. 이 화면을 닫지 말고 기다려 주세요.
            </p>
          </>
        )}
        {report.kind === "ready" && (
          <>
            <div className={styles.endRow}>
              <span className={styles.check} aria-hidden="true">
                ✓
              </span>
              <b>리포트가 준비됐어요</b>
            </div>
            <a className={styles.primary} href={href(report.reportId)}>
              리포트 보기
            </a>
          </>
        )}
        {report.kind === "unavailable" && (
          <p className={styles.reportNote}>실험용 서버(LAB_ENABLED)에서는 리포트를 만들지 않아요.</p>
        )}
        {report.kind === "error" && (
          <>
            <p className={styles.error}>리포트를 만들지 못했어요. ({report.message})</p>
            {needLogin ? (
              <LoginLink />
            ) : (
              <button type="button" className={styles.secondary} onClick={onRetry}>
                다시 시도
              </button>
            )}
          </>
        )}
      </div>

      {history.some((h) => h.who !== "system") && (
        <details className={styles.endLog}>
          <summary>대화 기록 보기</summary>
          <ol>
            {history
              .filter((h) => h.who !== "system")
              .map((h, i) => (
                <li key={i} className={h.who === "me" ? styles.endMe : undefined}>
                  <b>{h.who === "me" ? "나" : "면접관"}</b>
                  {h.text}
                </li>
              ))}
          </ol>
        </details>
      )}
    </section>
  );
}

/** 서버가 준 남은 시간(초)을 대략적인 말로 바꿉니다. 화면은 초를 세지 않아서 분 단위로만 보여 줍니다 */
function remainingLabel(sec: number): string {
  if (sec <= 0) return "없음";
  if (sec < 60) return "1분 미만";
  return `약 ${Math.ceil(sec / 60)}분`;
}

function MicOffIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0" />
      <path d="M12 18v3" />
      <path d="M4 4l16 16" />
    </svg>
  );
}

function MicIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0" />
      <path d="M12 18v3" />
    </svg>
  );
}
