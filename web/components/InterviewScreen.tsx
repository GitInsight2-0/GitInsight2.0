"use client";

import type { Action, BrainReply } from "@gitinsight/contracts";
import { useEffect, useRef, useState } from "react";
import CodeViewer from "@/components/CodeViewer";
import { ApiError, createSession, isAbort, postTurn } from "@/lib/voice/api";
import { DEFAULT_VAD } from "@/lib/voice/config";
import { explainError } from "@/lib/voice/errors";
import { connectGemini } from "@/lib/voice/gemini";
import { type SessionUi, type VoiceSession, startVoiceSession } from "@/lib/voice/session";

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
  useEffect(() => () => voiceAbort.current?.abort(), []);

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
        if (who === "user") push({ who: "me", text });
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
      },
      ended: (reason) => {
        voice.current = null;
        setVoiceState("idle");
        setMode("text");
        if (replyRef.current?.done) return;
        if (!voiceStoppedByUser.current && sessionRef.current) {
          setNotice(`음성 연결이 끊겨 텍스트로 이어 갑니다. 다시 시도하려면 "음성으로 바꾸기"를 누르세요. (${reason})`);
        }
      },
      latency: noop,
      filler: noop,
      delegation: noop,
      bargeIn: noop,
      bargeStop: noop,
      transcriptSimilarity: noop,
      delegationMissed: noop,
      turnSent: noop,
    };

    try {
      voice.current = await startVoiceSession("gemini", connectGemini, ui, {
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
    setVoiceState("idle");
    setMode("text");
    push({ who: "system", text: "텍스트로 바꿨습니다." });
  }

  // ── 화면 ──

  if (!sessionId && voiceState === "idle") {
    return (
      <main style={page}>
        <h1 style={{ fontSize: 22 }}>모의 기술면접</h1>
        <p style={{ color: "#666" }}>
          레포 분석 결과를 바탕으로 질문합니다. 음성으로 답하거나 텍스트로 답할 수 있고, 도중에 바꿀 수 있습니다.
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={() => void startVoice()} disabled={busy}>
            음성으로 시작
          </button>
          <button onClick={() => void startText()} disabled={busy}>
            텍스트로 시작
          </button>
        </div>
        <p style={{ color: "#666", fontSize: 13 }}>음성은 Chrome에서 이어폰을 끼고 쓰세요. 스피커로 들으면 AI가 자기 목소리를 듣고 끊길 수 있습니다.</p>
        <p role="status" aria-live="polite" style={{ color: "#c00" }}>
          {error}
        </p>
      </main>
    );
  }

  const canSwitchToVoice = mode === "text" && voiceState === "idle" && !busy && !reply?.done;

  return (
    <main style={page}>
      <header style={{ display: "flex", gap: 16, alignItems: "baseline", marginBottom: 12, flexWrap: "wrap" }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>모의 기술면접</h1>
        {reply && (
          <span style={{ color: "#666", fontSize: 14 }}>
            {ACTION_LABEL[reply.action]} · 주제 {reply.topic_id ?? "-"} · 깊이 {reply.depth} · 남은 시간{" "}
            {Math.floor(reply.remaining_sec / 60)}분 {reply.remaining_sec % 60}초
          </span>
        )}
        <span style={{ marginLeft: "auto", fontSize: 14 }}>
          {mode === "voice" ? (voiceState === "live" ? "음성 면접 중" : "음성 연결 중…") : "텍스트 면접 중"}
        </span>
      </header>

      {notice && (
        <p role="status" aria-live="polite" style={{ background: "#fff6d5", padding: "8px 12px", borderRadius: 8, fontSize: 14 }}>
          {notice}
        </p>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.2fr)", gap: 16 }}>
        <section style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div aria-live="polite" style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, fontSize: 16, lineHeight: 1.6 }}>
            {reply?.utterance ?? "면접을 준비하고 있습니다…"}
          </div>

          {reply?.done ? (
            <p style={{ color: "#666" }}>면접이 끝났습니다. 리포트 화면은 D의 작업(#20)과 연결할 예정입니다.</p>
          ) : mode === "voice" ? (
            <div style={{ border: "1px dashed #ccc", borderRadius: 8, padding: 12 }}>
              <p style={{ margin: "0 0 8px" }}>
                {voiceState === "live" ? "말씀하시면 듣고 있습니다. 답변을 마치면 잠시 기다려 주세요." : "마이크와 음성 AI를 연결하고 있습니다…"}
              </p>
              <button onClick={switchToText}>텍스트로 바꾸기</button>
            </div>
          ) : (
            <div>
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
                rows={5}
                disabled={busy}
                style={{ width: "100%", boxSizing: "border-box", fontSize: 15, padding: 10, borderRadius: 8, border: "1px solid #ccc" }}
              />
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 6 }}>
                <span role="status" aria-live="polite" style={{ color: "#c00", fontSize: 13, marginRight: "auto" }}>
                  {error}
                </span>
                <button onClick={() => void startVoice()} disabled={!canSwitchToVoice}>
                  음성으로 바꾸기
                </button>
                <button onClick={() => void submit()} disabled={busy || !draft.trim()}>
                  {busy ? "보내는 중…" : "답변 보내기"}
                </button>
              </div>
            </div>
          )}

          <details>
            <summary style={{ cursor: "pointer", color: "#666" }}>대화 기록 ({history.length})</summary>
            <ol style={{ paddingLeft: 20, fontSize: 14, lineHeight: 1.6 }}>
              {history.map((h, i) => (
                <li key={i} style={{ color: h.who === "system" ? "#888" : "#111" }}>
                  <b>{h.who === "interviewer" ? "면접관" : h.who === "me" ? "나" : "·"}</b> {h.text}
                </li>
              ))}
            </ol>
          </details>
        </section>

        <CodeViewer repo={repo} target={reply?.highlight ?? null} />
      </div>
    </main>
  );
}

const page = { fontFamily: "system-ui, sans-serif", maxWidth: 1200, margin: "24px auto", padding: "0 16px" } as const;
