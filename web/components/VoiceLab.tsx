"use client";

import type { BrainReply } from "@gitinsight/contracts";
import { useRef, useState } from "react";
import { DEFAULT_VAD, TEST_SCRIPT, VAD_LIMITS, type VadSettings } from "@/lib/voice/config";
import { connectGemini } from "@/lib/voice/gemini";
import { connectOpenAI } from "@/lib/voice/openai";
import { type LogWho, startVoiceSession } from "@/lib/voice/session";
import type { Provider } from "@/lib/voice/types";

interface LogLine {
  sec: number;
  who: LogWho;
  text: string;
}

const CONNECT = { openai: connectOpenAI, gemini: connectGemini } as const;
const NAMES: Record<Provider, string> = { openai: "OpenAI Realtime (WebRTC)", gemini: "Gemini Live (WebSocket)" };

/** 브라우저 오류를 사람이 바로 이해할 수 있는 문구로 바꿉니다. */
function explainError(e: unknown): string {
  const name = (e as { name?: unknown } | null)?.name;
  if (name === "NotAllowedError") {
    return "마이크 권한이 막혀 있습니다. Chrome에서 이 페이지를 열고 주소창 왼쪽 아이콘에서 마이크를 허용하세요. (Claude 앱 안의 브라우저는 마이크를 쓸 수 없습니다)";
  }
  if (name === "NotFoundError") return "마이크를 찾을 수 없습니다. 이어폰이나 마이크 연결을 확인하세요.";
  return String(e);
}

function stats(xs: number[]) {
  if (!xs.length) return { n: 0, median: null as number | null, p90: null as number | null };
  const s = [...xs].sort((a, b) => a - b);
  const q = (p: number) => s[Math.min(s.length - 1, Math.floor(p * s.length))];
  return { n: s.length, median: q(0.5), p90: q(0.9) };
}

export default function VoiceLab({ analysisId }: { analysisId: string }) {
  const [provider, setProvider] = useState<Provider>("gemini");
  const [labDelayMs, setLabDelayMs] = useState(800);
  const [status, setStatus] = useState("대기");
  const [running, setRunning] = useState(false);
  const [log, setLog] = useState<LogLine[]>([]);
  const [reply, setReply] = useState<BrainReply | null>(null);
  const [latencies, setLatencies] = useState<number[]>([]);
  const [delegations, setDelegations] = useState<number[]>([]);
  const [bargeIns, setBargeIns] = useState(0);
  const [similarities, setSimilarities] = useState<number[]>([]);
  const [missed, setMissed] = useState(0);
  const [vad, setVad] = useState<VadSettings>(DEFAULT_VAD);
  const [filler, setFiller] = useState(true);
  const [fillers, setFillers] = useState<number[]>([]);
  const session = useRef<{ stop(): void } | null>(null);
  const t0 = useRef(0);

  const add = (who: LogWho, text: string) =>
    setLog((l) => [...l, { sec: Math.round((performance.now() - t0.current) / 100) / 10, who, text }]);

  async function start() {
    setLog([]);
    setReply(null);
    setLatencies([]);
    setDelegations([]);
    setBargeIns(0);
    setSimilarities([]);
    setMissed(0);
    setFillers([]);
    setRunning(true);
    t0.current = performance.now();
    try {
      session.current = await startVoiceSession(
        provider,
        CONNECT[provider],
        {
          log: add,
          status: setStatus,
          reply: (r) => {
            setReply(r);
            const where = r.highlight ? ` → ${r.highlight.path}:${r.highlight.start_line}-${r.highlight.end_line}` : "";
            add("system", `${r.action}${where}${r.done ? " · 종료" : ""}`);
          },
          latency: (ms) => setLatencies((x) => [...x, ms]),
          filler: (ms) => setFillers((x) => [...x, ms]),
          delegation: (ms) => setDelegations((x) => [...x, ms]),
          bargeIn: () => {
            setBargeIns((n) => n + 1);
            add("system", "끼어들기 감지");
          },
          delegationMissed: () => setMissed((n) => n + 1),
          transcriptSimilarity: (ratio) => {
            setSimilarities((x) => [...x, ratio]);
            add("system", `받아쓰기 vs 도구 인자 유사도 ${ratio.toFixed(2)}`);
          },
        },
        { analysisId, labDelayMs, scenario: "g1_script", vad, filler },
      );
    } catch (e) {
      add("error", explainError(e));
      setStatus("실패");
      setRunning(false);
    }
  }

  function stop() {
    session.current?.stop();
    session.current = null;
    setRunning(false);
  }

  const lat = stats(latencies);
  const del = stats(delegations);
  const fil = stats(fillers);
  const sim = {
    n: similarities.length,
    min: similarities.length ? Math.min(...similarities) : null,
    mean: similarities.length ? Math.round((similarities.reduce((a, b) => a + b, 0) / similarities.length) * 100) / 100 : null,
  };
  const errors = log.filter((l) => l.who === "error");
  // 자막 원문은 빼고 수치와 오류만 복사합니다 (docs/evals/voice-g1.md에 붙여 넣는 용도).
  const result = {
    provider,
    lab_delay_ms: labDelayMs,
    vad,
    filler,
    tested_at: new Date().toISOString(),
    // 말 끝 → 서버가 정한 발화 첫소리
    latency_ms: lat,
    // 말 끝 → 맞장구 첫소리 (맞장구가 나온 턴만)
    filler_ms: fil,
    delegation_ms: del,
    barge_ins: bargeIns,
    // 받아쓰기와 음성 AI 도구 인자의 유사도. 1에서 멀수록 모델이 답변을 다듬거나 요약한 것입니다.
    transcript_similarity: sim,
    // 음성 AI가 서버를 부르지 않고 직접 답한 횟수
    delegation_missed: missed,
    errors: errors.map((e) => e.text),
  };

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 1100, margin: "24px auto", padding: "0 16px" }}>
      <h1 style={{ fontSize: 22, marginBottom: 4 }}>G1 음성 비교 PoC</h1>
      <p style={{ color: "#666", marginTop: 0 }}>
        이어폰을 끼고 테스트합니다. 면접 두뇌는 FastAPI의 대본 두뇌(LAB_ENABLED)입니다. 상태: <b>{status}</b>
      </p>

      <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 16, flexWrap: "wrap" }}>
        <select value={provider} onChange={(e) => setProvider(e.target.value as Provider)} disabled={running}>
          {(Object.keys(NAMES) as Provider[]).map((p) => (
            <option key={p} value={p}>
              {NAMES[p]}
            </option>
          ))}
        </select>
        <label>
          판단 지연 흉내{" "}
          <input
            type="number"
            min={0}
            max={5000}
            step={100}
            value={labDelayMs}
            onChange={(e) => setLabDelayMs(Number(e.target.value))}
            disabled={running}
            style={{ width: 80 }}
          />{" "}
          ms
        </label>
        <label title="이만큼 조용하면 답변이 끝난 것으로 봅니다. 길수록 덜 끼어들지만 응답이 늦어집니다.">
          말 끝 침묵{" "}
          <input
            type="number"
            min={VAD_LIMITS.minSilenceMs}
            max={VAD_LIMITS.maxSilenceMs}
            step={100}
            value={vad.silenceMs}
            onChange={(e) => setVad((v) => ({ ...v, silenceMs: Number(e.target.value) }))}
            disabled={running}
            style={{ width: 80 }}
          />{" "}
          ms
        </label>
        <label title="Gemini만 적용됩니다. 기본값은 HIGH(빨리 끝냄)입니다.">
          말 끝 민감도{" "}
          <select
            value={vad.endSensitivity}
            onChange={(e) => setVad((v) => ({ ...v, endSensitivity: e.target.value as VadSettings["endSensitivity"] }))}
            disabled={running}
          >
            <option value="low">LOW (덜 끊음)</option>
            <option value="high">HIGH</option>
            <option value="default">제공사 기본값</option>
          </select>
        </label>
        <label title="도구를 부르기 전에 &quot;네.&quot;처럼 짧게 먼저 반응합니다.">
          <input type="checkbox" checked={filler} onChange={(e) => setFiller(e.target.checked)} disabled={running} /> AI
          맞장구
        </label>
        <button onClick={start} disabled={running}>
          시작
        </button>
        <button onClick={stop} disabled={!running}>
          종료
        </button>
        <button onClick={() => navigator.clipboard.writeText(JSON.stringify(result, null, 2))}>결과 JSON 복사</button>
      </div>

      <section style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 16 }}>
        <Stat label="말 끝 → 맞장구 중앙값" value={fil.median} unit="ms" sub={`p90 ${fil.p90 ?? "-"} · ${fil.n}회`} />
        <Stat label="말 끝 → 질문 중앙값" value={lat.median} unit="ms" sub={`p90 ${lat.p90 ?? "-"} · ${lat.n}회`} />
        <Stat label="위임 왕복 중앙값" value={del.median} unit="ms" sub={`p90 ${del.p90 ?? "-"} · ${del.n}회`} />
        <Stat label="끼어들기 감지" value={bargeIns} unit="회" sub="AI가 실제로 멈췄는지는 귀로 확인" />
        <Stat
          label="받아쓰기 vs 도구 인자"
          value={sim.mean}
          unit="평균 유사도"
          sub={`최저 ${sim.min ?? "-"} · ${sim.n}회`}
        />
        <Stat label="위임 누락" value={missed} unit="회" sub="AI가 서버를 부르지 않고 직접 답한 횟수" />
        <Stat label="오류" value={errors.length} unit="건" sub="" />
      </section>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <section>
          <h2 style={{ fontSize: 16 }}>대화 기록</h2>
          <div style={{ height: 420, overflow: "auto", border: "1px solid #ddd", borderRadius: 8, padding: 8, fontSize: 14 }}>
            {log.map((l, i) => (
              <div key={i} style={{ color: l.who === "error" ? "#c00" : l.who === "system" ? "#888" : "#111", marginBottom: 4 }}>
                <span style={{ color: "#aaa" }}>{l.sec.toFixed(1)}s</span> <b>{WHO[l.who]}</b> {l.text}
              </div>
            ))}
          </div>
        </section>
        <section>
          <h2 style={{ fontSize: 16 }}>서버가 정한 현재 발화</h2>
          {reply ? (
            <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 12, fontSize: 14 }}>
              <div>
                <b>{reply.action}</b> · 주제 {reply.topic_id ?? "-"} · 깊이 {reply.depth} · 남은 시간 {reply.remaining_sec}초
              </div>
              <p>{reply.utterance}</p>
              {reply.highlight ? (
                // 코드 본문 뷰어는 T8에서 붙입니다. 지금은 위치만 보여 줍니다 (런북의 코드 뷰어 1차 대비책과 같은 표시).
                <code style={{ background: "#fff6d5", padding: "2px 6px", borderRadius: 4 }}>
                  {reply.highlight.path}:{reply.highlight.start_line}-{reply.highlight.end_line} · {reply.highlight.name}
                </code>
              ) : (
                <span style={{ color: "#888" }}>하이라이트 없음</span>
              )}
            </div>
          ) : (
            <p style={{ color: "#888" }}>아직 없음</p>
          )}
          <h2 style={{ fontSize: 16, marginTop: 16 }}>테스트 대본 (질문이 나올 때마다 순서대로)</h2>
          <ol style={{ fontSize: 14, lineHeight: 1.6 }}>
            {TEST_SCRIPT.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ol>
        </section>
      </div>
    </main>
  );
}

const WHO: Record<LogWho, string> = { user: "나", ai: "AI", system: "·", error: "오류" };

function Stat({ label, value, unit, sub }: { label: string; value: number | null; unit: string; sub: string }) {
  return (
    <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 12 }}>
      <div style={{ fontSize: 12, color: "#666" }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 600 }}>
        {value ?? "-"}
        <span style={{ fontSize: 13, fontWeight: 400 }}> {unit}</span>
      </div>
      <div style={{ fontSize: 12, color: "#888" }}>{sub}</div>
    </div>
  );
}
