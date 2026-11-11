"use client";

import type { Action, BrainReply } from "@gitinsight/contracts";
import { useRef, useState } from "react";
import CodeViewer from "@/components/CodeViewer";
import { ApiError, createSession, postTurn } from "@/lib/voice/api";

/**
 * 면접 화면 (T8, #30). 지금은 텍스트 모드만 있습니다.
 * 음성 모드와 도중 전환은 다음 단계에서 lib/voice/session.ts를 붙입니다.
 *
 * 다음 질문, 반복, 힌트, 종료는 서버가 정하고 이 화면은 보여 주기만 합니다.
 */

interface HistoryItem {
  who: "interviewer" | "me" | "system";
  text: string;
}

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

export default function InterviewScreen({ analysisId, repo }: { analysisId: string; repo: string | null }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [reply, setReply] = useState<BrainReply | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pendingTurnId = useRef<string | null>(null);

  const push = (item: HistoryItem) => setHistory((h) => [...h, item]);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const created = await createSession({ analysis_id: analysisId, mode: "text" });
      setSessionId(created.session_id);
      setReply(created.first);
      setHistory([{ who: "interviewer", text: created.first.utterance }]);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    const transcript = draft.trim();
    if (!sessionId || !transcript || busy || reply?.done) return;
    setBusy(true);
    setError(null);
    // 같은 답변을 다시 보낼 때(네트워크 오류 뒤 재시도) 같은 turn_id를 써서 서버가 한 번만 반영하게 합니다.
    const turnId = pendingTurnId.current ?? crypto.randomUUID();
    pendingTurnId.current = turnId;
    try {
      const next = await postTurn(sessionId, {
        turn_id: turnId,
        transcript,
        source: "text",
        speech_started_at: null,
        speech_ended_at: null,
      });
      pendingTurnId.current = null;
      push({ who: "me", text: transcript });
      push({ who: "interviewer", text: next.utterance });
      setReply(next);
      setDraft("");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        pendingTurnId.current = null;
        push({ who: "system", text: "이미 끝난 면접입니다." });
        setReply((r) => (r ? { ...r, done: true } : r));
      } else {
        setError(`답변을 보내지 못했습니다. 다시 보내 주세요. (${String((e as Error).message ?? e)})`);
      }
    } finally {
      setBusy(false);
    }
  }

  if (!sessionId) {
    return (
      <main style={page}>
        <h1 style={{ fontSize: 22 }}>모의 기술면접</h1>
        <p style={{ color: "#666" }}>레포 분석 결과를 바탕으로 질문합니다. 지금은 텍스트로 답합니다.</p>
        <button onClick={start} disabled={busy}>
          면접 시작
        </button>
        {error && <p style={{ color: "#c00" }}>{error}</p>}
      </main>
    );
  }

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
      </header>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.2fr)", gap: 16 }}>
        <section style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, fontSize: 16, lineHeight: 1.6 }}>
            {reply?.utterance}
          </div>

          {reply?.done ? (
            <p style={{ color: "#666" }}>면접이 끝났습니다. 리포트 화면은 D의 작업과 연결할 예정입니다.</p>
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
                placeholder="답변을 입력하세요 (Enter 보내기, Shift+Enter 줄바꿈)"
                rows={5}
                disabled={busy}
                style={{ width: "100%", boxSizing: "border-box", fontSize: 15, padding: 10, borderRadius: 8, border: "1px solid #ccc" }}
              />
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 6 }}>
                {error && <span style={{ color: "#c00", fontSize: 13, marginRight: "auto" }}>{error}</span>}
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
