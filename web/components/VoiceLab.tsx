"use client";

import type { BrainReply } from "@gitinsight/contracts";
import { useEffect, useRef, useState } from "react";
import { isAbort } from "@/lib/voice/api";
import { explainError } from "@/lib/voice/errors";
import { DEFAULT_VAD, TEST_SCRIPT, VAD_LIMITS, type VadSettings } from "@/lib/voice/config";
import { connectGemini } from "@/lib/voice/gemini";
import { type LogWho, type VoiceSession, startVoiceSession } from "@/lib/voice/session";
import { summarize } from "@/lib/voice/stats";
import { PROVIDER } from "@/lib/voice/types";

interface LogLine {
  sec: number;
  who: LogWho;
  text: string;
}

/** G1 기준: 끊으면 이 시간 안에 AI가 멈춰야 합니다 */
const BARGE_STOP_TARGET_MS = 500;

export default function VoiceLab({ analysisId }: { analysisId: string }) {
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
  // #28 측정: 모델 맞장구는 위임 누락을 늘려 기본으로 끕니다.
  const [filler, setFiller] = useState(false);
  const [fillers, setFillers] = useState<number[]>([]);
  /** 끼어들기 뒤 AI가 멈추기까지 (ms). null은 제한 시간 안에 안 멈춤 */
  const [bargeStops, setBargeStops] = useState<(number | null)[]>([]);
  /** 지금 답하고 있는 대본 줄 (1부터). 서버로 턴을 보낼 때마다 하나씩 넘어갑니다. */
  const [scriptLine, setScriptLine] = useState(1);
  const scriptLineRef = useRef(1);
  const session = useRef<VoiceSession | null>(null);
  const abort = useRef<AbortController | null>(null);
  const t0 = useRef(0);

  // 페이지를 떠나면 마이크와 연결을 닫습니다.
  useEffect(() => () => abort.current?.abort(), []);

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
    setBargeStops([]);
    setScriptLine(1);
    scriptLineRef.current = 1;
    setRunning(true);
    t0.current = performance.now();
    const ac = new AbortController();
    abort.current = ac;
    try {
      session.current = await startVoiceSession(
        connectGemini,
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
          bargeStop: (ms) => {
            setBargeStops((x) => [...x, ms]);
            add("system", ms === null ? "끼어들기 뒤 AI가 멈추지 않음" : `끼어들기 뒤 AI 멈춤 ${ms}ms`);
          },
          turnSent: () => {
            scriptLineRef.current = Math.min(scriptLineRef.current + 1, TEST_SCRIPT.length);
            setScriptLine(scriptLineRef.current);
          },
          ended: (reason) => {
            add("system", reason);
            session.current = null;
            setRunning(false);
          },
          delegationMissed: () => setMissed((n) => n + 1),
          transcriptSimilarity: (ratio) => {
            setSimilarities((x) => [...x, ratio]);
            add("system", `받아쓰기 vs 도구 인자 유사도 ${ratio.toFixed(2)}`);
          },
        },
        {
          analysisId,
          labDelayMs,
          // 대본 줄별로 나눠 볼 수 있게 줄 번호를 붙입니다 (예: g1_s3).
          scenario: () => `g1_s${scriptLineRef.current}`,
          vad,
          filler,
          signal: ac.signal,
        },
      );
    } catch (e) {
      // 사용자가 멈춘 것은 오류로 기록하지 않습니다.
      if (isAbort(e)) add("system", "시작 중에 멈췄습니다");
      else {
        add("error", explainError(e));
        setStatus("실패");
      }
      setRunning(false);
    }
  }

  function stop() {
    // 연결 중이면 신호로 멈추고, 진행 중이면 세션을 닫습니다. 둘 다 같은 정리 경로를 탑니다.
    abort.current?.abort();
    session.current?.stop();
    session.current = null;
    setRunning(false);
  }

  const lat = summarize(latencies);
  const del = summarize(delegations);
  const fil = summarize(fillers);
  const stopped = bargeStops.filter((x): x is number => x !== null);
  const barge = {
    n: bargeStops.length,
    within_target: stopped.filter((x) => x <= BARGE_STOP_TARGET_MS).length,
    not_stopped: bargeStops.length - stopped.length,
    stop_ms: summarize(stopped),
  };
  const sim = {
    n: similarities.length,
    min: similarities.length ? Math.min(...similarities) : null,
    mean: similarities.length ? Math.round((similarities.reduce((a, b) => a + b, 0) / similarities.length) * 100) / 100 : null,
  };
  const errors = log.filter((l) => l.who === "error");
  // 자막 원문은 빼고 수치와 오류만 복사합니다 (docs/evals/voice-g1.md에 붙여 넣는 용도).
  const result = {
    provider: PROVIDER,
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
    // 끼어들기 뒤 AI가 멈추기까지. within_target은 500ms 안에 멈춘 횟수
    barge_stop: barge,
    // 받아쓰기와 음성 AI 도구 인자의 유사도. 1에서 멀수록 모델이 답변을 다듬거나 요약한 것입니다.
    transcript_similarity: sim,
    // 음성 AI가 서버를 부르지 않고 직접 답한 횟수
    delegation_missed: missed,
    errors: errors.map((e) => e.text),
  };

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 1100, margin: "24px auto", padding: "0 16px" }}>
      <h1 style={{ fontSize: 22, marginBottom: 4 }}>음성 측정 (Gemini Live)</h1>
      <p style={{ color: "#666", marginTop: 0 }}>
        이어폰을 끼고 테스트합니다. 면접 두뇌는 FastAPI의 대본 두뇌(LAB_ENABLED)입니다. 상태: <b>{status}</b>
      </p>

      <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 16, flexWrap: "wrap" }}>
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
        <Stat label="말 끝 → 맞장구 중앙값" value={fil.median} unit="ms" sub={`p90 ${fil.p90 ?? "-"} · p95 ${fil.p95 ?? "-"} · ${fil.n}회`} />
        <Stat label="말 끝 → 질문 중앙값" value={lat.median} unit="ms" sub={`p90 ${lat.p90 ?? "-"} · p95 ${lat.p95 ?? "-"} · ${lat.n}회`} />
        <Stat label="위임 왕복 중앙값" value={del.median} unit="ms" sub={`p90 ${del.p90 ?? "-"} · p95 ${del.p95 ?? "-"} · ${del.n}회`} />
        <Stat
          label={`끼어들기 뒤 ${BARGE_STOP_TARGET_MS}ms 안에 멈춤`}
          value={barge.n ? barge.within_target : null}
          unit={`/ ${barge.n}회`}
          sub={`멈추기까지 중앙값 ${barge.stop_ms.median ?? "-"}ms · 안 멈춤 ${barge.not_stopped}회 · 감지 ${bargeIns}회`}
        />
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
            {TEST_SCRIPT.map((s, i) => (
              <li key={s} style={{ fontWeight: running && i + 1 === scriptLine ? 600 : 400 }}>
                {s}
              </li>
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
