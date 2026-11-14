"use client";

import type { Action, BrainReply } from "@gitinsight/contracts";
import { useEffect, useRef, useState } from "react";
import CodeViewer from "@/components/CodeViewer";
import { ApiError, createSession, isAbort, postTurn } from "@/lib/voice/api";
import { DEFAULT_VAD } from "@/lib/voice/config";
import { explainError } from "@/lib/voice/errors";
import { connectGemini } from "@/lib/voice/gemini";
import { type AudioLevels, type SessionUi, type VoiceSession, startVoiceSession } from "@/lib/voice/session";
import { USER_SILENCE_MS } from "@/lib/voice/meter";
import styles from "./interview.module.css";
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
}

type Mode = "text" | "voice";
type VoiceState = "idle" | "connecting" | "live";
/** 가운데 음성 표시가 보여 줄 상태 */
type Stage = "connecting" | "listening" | "thinking" | "speaking" | "text" | "done";

const STAGE_LABEL: Record<Stage, string> = {
  connecting: "연결하고 있어요",
  listening: "듣고 있어요",
  thinking: "답변을 살펴보고 있어요",
  speaking: "말하는 중",
  text: "텍스트로 답하는 중",
  done: "면접이 끝났어요",
};

/** 말 끝 판정 침묵 (초). 안내 문구에 씁니다 */
const SILENCE_SEC = DEFAULT_VAD.silenceMs / 1000;
/**
 * 측정기가 "말이 멈췄다"고 본 뒤 이만큼 더 조용하면 "답변을 살펴보고 있어요"로 바꿉니다.
 * 측정기는 0.5초 침묵에서 멈춤으로 보고, 음성 AI는 2초 침묵에서 답변이 끝났다고 봅니다. 그 차이만큼 기다립니다.
 */
const THINKING_AFTER_MS = Math.max(0, DEFAULT_VAD.silenceMs - USER_SILENCE_MS);

const ACTION_LABEL: Record<Action, string> = {
  ASK: "질문",
  FOLLOW_UP: "꼬리질문",
  PROBE: "확인 질문",
  HINT: "힌트",
  REPEAT: "다시 말씀드림",
  CLARIFY: "설명",
  NEXT_TOPIC: "다음 주제",
  WRAP_UP: "마무리",
};

/** 아무것도 하지 않는 측정 콜백 (측정은 /lab/voice에서 봅니다. 서버 /api/metrics에는 진행기가 그대로 보냅니다) */
const noop = () => undefined;

export default function InterviewScreen({ analysisId, repo }: { analysisId: string; repo: string | null }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [reply, setReply] = useState<BrainReply | null>(null);
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
  /** 음성 턴을 서버로 보내고 답을 기다리는 중 */
  const [thinking, setThinking] = useState(false);
  const [aiSpeaking, setAiSpeaking] = useState(false);
  const [levels, setLevels] = useState<AudioLevels | null>(null);
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

  const push = (item: HistoryItem) => setHistory((h) => [...h, item]);
  const showReply = (r: BrainReply) => {
    replyRef.current = r;
    setReply(r);
  };

  // 페이지를 떠나면 마이크와 음성 연결을 닫습니다. 서버 세션은 그대로 둡니다.
  useEffect(
    () => () => {
      voiceAbort.current?.abort();
      clearThinkingTimer();
    },
    [],
  );

  function clearThinkingTimer() {
    if (thinkingTimer.current) clearTimeout(thinkingTimer.current);
    thinkingTimer.current = null;
  }

  // ── 텍스트 ──

  async function startText() {
    setBusy(true);
    setError(null);
    try {
      const created = await createSession({ analysis_id: analysisId, mode: "text" });
      sessionRef.current = created.session_id;
      setSessionId(created.session_id);
      showReply(created.first);
      setHistory([{ who: "interviewer", text: created.first.utterance }]);
    } catch (e) {
      setError(String((e as Error).message ?? e));
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
      push({ who: "interviewer", text: next.utterance });
      showReply(next);
      setDraft("");
    } catch (e) {
      // 서버(api/routes/sessions.py)는 /turns에서 409를 "이미 끝난 세션"에만 씁니다.
      if (e instanceof ApiError && e.status === 409) {
        pendingTurn.current = null;
        push({ who: "system", text: "이미 끝난 면접입니다." });
        const r = replyRef.current;
        if (r) showReply({ ...r, done: true });
      } else {
        setError(`답변을 보내지 못했습니다. 다시 보내 주세요. (${String((e as Error).message ?? e)})`);
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
    setError(null);
    setNotice(null);
    setMode("voice");
    setVoiceState("connecting");

    const ui: SessionUi = {
      log: (who, text) => {
        // 사용자 받아쓰기만 대화 기록에 남깁니다. 면접관 발화는 서버가 정한 문장(reply)으로 남깁니다.
        if (who === "user") {
          push({ who: "me", text });
          setLastAnswer(text);
        }
      },
      status: (s) => {
        if (s === "연결됨") setVoiceState("live");
      },
      started: (id, current) => {
        if (!sessionRef.current) {
          sessionRef.current = id;
          setSessionId(id);
          setHistory([{ who: "interviewer", text: current.utterance }]);
          showReply(current);
        }
      },
      reply: (r) => {
        // 이어받은 세션의 현재 발화는 이미 기록에 있으므로 다시 넣지 않습니다.
        if (r === replyRef.current) return;
        push({ who: "interviewer", text: r.utterance });
        showReply(r);
        // "답변을 살펴보고 있어요"는 AI가 실제로 말하기 시작할 때 끕니다 (speaking).
        setLastAnswer(null);
      },
      speaking: (who, on) => {
        clearThinkingTimer();
        if (who === "ai") {
          setAiSpeaking(on);
          // AI가 말하기 시작하면 기다림은 끝난 것입니다 (서버 응답 실패 때 나오는 다시 말해 달라는 문장 포함).
          if (on) setThinking(false);
          return;
        }
        if (on) {
          // 다시 말하기 시작했으면 아직 답변 중입니다.
          setThinking(false);
          return;
        }
        // 말을 멈췄습니다. 음성 AI가 답변 끝으로 볼 만큼 조용하면 기다리는 중으로 표시합니다.
        thinkingTimer.current = setTimeout(() => setThinking(true), THINKING_AFTER_MS);
      },
      audio: setLevels,
      ended: (reason) => {
        voice.current = null;
        resetVoiceView();
        setVoiceState("idle");
        setMode("text");
        if (replyRef.current?.done) return;
        if (!voiceStoppedByUser.current && sessionRef.current) {
          setNotice(`음성 연결이 끊겨 텍스트로 이어 갑니다. 다시 시도하려면 위의 "음성"을 누르세요. (${reason})`);
        }
      },
      latency: noop,
      filler: noop,
      delegation: noop,
      bargeIn: noop,
      bargeStop: noop,
      transcriptSimilarity: noop,
      delegationMissed: noop,
      turnSent: () => setThinking(true),
    };

    try {
      voice.current = await startVoiceSession(connectGemini, ui, {
        analysisId,
        labDelayMs: 0,
        scenario: () => "interview",
        vad: DEFAULT_VAD,
        filler: false,
        signal: ac.signal,
        existing,
      });
    } catch (e) {
      // 진행기가 이미 ended로 텍스트 모드 전환을 처리했습니다. 세션을 못 만든 경우만 여기서 알립니다.
      voice.current = null;
      resetVoiceView();
      setVoiceState("idle");
      setMode("text");
      if (voiceStoppedByUser.current || isAbort(e)) return;
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
    setAiSpeaking(false);
    setLastAnswer(null);
  }

  // ── 화면 ──

  if (!sessionId && voiceState === "idle") {
    return (
      <div className={styles.page}>
        <header className={styles.topbar}>
          <span className={styles.brand}>GitInsight</span>
        </header>
        <main className={styles.layout}>
          <section className={styles.stage}>
            <div className={styles.intro}>
              <h1>모의 기술면접</h1>
              <p>레포 분석 결과를 바탕으로 질문합니다. 음성으로 답하거나 텍스트로 답할 수 있고, 도중에 바꿀 수 있습니다.</p>
              <div className={styles.introActions}>
                <button type="button" className={styles.primary} onClick={() => void startVoice()} disabled={busy}>
                  음성으로 시작
                </button>
                <button type="button" className={styles.secondary} onClick={() => void startText()} disabled={busy}>
                  텍스트로 시작
                </button>
              </div>
              <p className={styles.note}>
                <MicIcon />
                음성은 Chrome에서 이어폰을 끼고 쓰세요. 스피커로 들으면 AI가 자기 목소리를 듣고 끊길 수 있습니다.
              </p>
              <p role="status" aria-live="polite" className={styles.error}>
                {error}
              </p>
            </div>
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
        : thinking
          ? "thinking"
          : aiSpeaking
            ? "speaking"
            : "listening";
  const quiet = stage === "text" || stage === "done";
  const orbClass = [styles.orb, quiet ? styles.orbIdle : "", stage === "thinking" || stage === "connecting" ? styles.orbThinking : ""]
    .filter(Boolean)
    .join(" ");
  const canSwitchToVoice = mode === "text" && voiceState === "idle" && !busy && !done;

  return (
    <div className={styles.page}>
      <header className={styles.topbar}>
        <span className={styles.brand}>GitInsight</span>
        {reply && (
          <span className={styles.progress}>
            <span className={styles.chip}>{ACTION_LABEL[reply.action]}</span>
          </span>
        )}
        <div className={styles.controls}>
          {reply && !done && (
            <span className={styles.time}>
              남은 시간 <b>약 {Math.max(1, Math.ceil(reply.remaining_sec / 60))}분</b>
            </span>
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
        </div>
      </header>

      <main className={styles.layout}>
        <section aria-label="면접" className={styles.stage}>
          {notice && (
            <p role="status" aria-live="polite" className={styles.notice}>
              {notice}
            </p>
          )}

          <div className={styles.voice}>
            <div className={orbClass}>
              <Waveform levels={levels} active={stage === "listening" || stage === "speaking"} />
            </div>
            <span role="status" aria-live="polite" className={quiet ? `${styles.state} ${styles.stateIdle}` : styles.state}>
              {STAGE_LABEL[stage]}
            </span>
          </div>

          <p aria-live="polite" className={styles.question}>
            {reply?.utterance ?? "면접을 준비하고 있습니다…"}
          </p>

          {done ? (
            <p className={styles.done}>수고하셨습니다.</p>
          ) : mode === "voice" ? (
            <>
              <div className={styles.answer}>
                <div className={styles.label}>내 답변</div>
                <p className={styles.answerText}>{lastAnswer ?? (stage === "listening" ? "말씀하시면 여기에 받아 적습니다." : "")}</p>
              </div>
              <p className={styles.hint}>
                <MicIcon />
                말을 마치고 {SILENCE_SEC}초 쉬면 답변이 전송돼요
              </p>
            </>
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
                placeholder="답변을 입력하세요 (Enter 보내기, Shift+Enter 줄바꿈)"
                rows={4}
                disabled={busy}
              />
              <div className={styles.composerRow}>
                <span role="status" aria-live="polite" className={styles.error}>
                  {error}
                </span>
                <button type="button" className={styles.primary} onClick={() => void submit()} disabled={busy || !draft.trim()}>
                  {busy ? "보내는 중…" : "답변 보내기"}
                </button>
              </div>
            </div>
          )}

          {history.length > 1 && (
            <details className={styles.history}>
              <summary>대화 기록 ({history.length})</summary>
              <ol>
                {history.map((h, i) => (
                  <li key={i} className={h.who === "system" ? styles.historySystem : undefined}>
                    <span className={styles.who}>{h.who === "interviewer" ? "면접관" : h.who === "me" ? "나" : "·"}</span>
                    {h.text}
                  </li>
                ))}
              </ol>
            </details>
          )}
        </section>

        <CodeViewer repo={repo} target={reply?.highlight ?? null} />
      </main>
    </div>
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
