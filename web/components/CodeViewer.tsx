"use client";

import type { SymbolRef } from "@gitinsight/contracts";
import { useEffect, useRef, useState } from "react";
import { fetchFileLines } from "@/lib/github/code";

/** 강조 범위 앞뒤로 보여 줄 줄 수 */
const CONTEXT_LINES = 4;

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  /** key는 이 내용이 어느 파일의 것인지입니다. 파일이 바뀐 첫 렌더에서 옛 내용을 새 줄 범위로 그리지 않게 비교합니다. */
  | { kind: "ok"; key: string; lines: string[] }
  | { kind: "error"; message: string };

/**
 * 서버가 준 highlight(경로, 줄 범위, 커밋)만 강조합니다. 줄 번호를 화면에서 바꾸지 않습니다.
 * 코드를 못 받으면 위치만 보여 줍니다 (런북 "코드 뷰어" 1차 대비책).
 */
export default function CodeViewer({ repo, target }: { repo: string | null; target: SymbolRef | null }) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const firstLineRef = useRef<HTMLDivElement | null>(null);
  const key = target ? `${repo}@${target.commit_sha}:${target.path}` : "";

  useEffect(() => {
    if (!target || !repo) {
      setState({ kind: "idle" });
      return;
    }
    let alive = true;
    setState({ kind: "loading" });
    fetchFileLines(repo, target.commit_sha, target.path)
      .then((lines) => alive && setState({ kind: "ok", key, lines }))
      .catch((e: unknown) => alive && setState({ kind: "error", message: String((e as Error).message ?? e) }));
    return () => {
      alive = false;
    };
    // 같은 파일이면 다시 받지 않습니다. 줄 범위만 바뀌면 아래에서 다시 그립니다.
  }, [key]);

  // 강조 줄이 보이게 코드 상자 안만 스크롤합니다 (scrollIntoView는 페이지 전체를 움직여 답변 입력 중 화면이 튑니다).
  useEffect(() => {
    const box = scrollRef.current;
    const line = firstLineRef.current;
    if (!box || !line) return;
    // 상자가 position: relative라서 줄의 offsetTop은 상자 기준입니다.
    box.scrollTop = Math.max(0, line.offsetTop - box.clientHeight / 3);
  }, [state, target?.start_line]);

  if (!target) {
    return <Frame title="관련 코드">이 질문에는 강조할 코드가 없습니다.</Frame>;
  }

  const location = `${target.path}:${target.start_line}-${target.end_line} · ${target.name}`;
  if (!repo || state.kind === "error") {
    return (
      <Frame title={location}>
        <p style={{ margin: 0, color: "#666" }}>
          코드를 불러오지 못해 위치만 보여 줍니다{state.kind === "error" ? ` (${state.message})` : ""}.
        </p>
      </Frame>
    );
  }
  if (state.kind !== "ok" || state.key !== key) return <Frame title={location}>코드를 불러오는 중…</Frame>;

  const from = Math.max(1, target.start_line - CONTEXT_LINES);
  const to = Math.min(state.lines.length, target.end_line + CONTEXT_LINES);
  const rows: { no: number; text: string; hit: boolean }[] = [];
  for (let no = from; no <= to; no++) {
    rows.push({ no, text: state.lines[no - 1] ?? "", hit: no >= target.start_line && no <= target.end_line });
  }
  const outOfRange = target.end_line > state.lines.length;

  return (
    <Frame title={location}>
      {outOfRange && (
        <p style={{ margin: "0 0 8px", color: "#c00", fontSize: 13 }}>
          파일이 {state.lines.length}줄이라 강조 범위({target.start_line}-{target.end_line})와 맞지 않습니다. 분석 커밋을 확인하세요.
        </p>
      )}
      <div ref={scrollRef} style={{ position: "relative", maxHeight: 420, overflow: "auto", fontFamily: "ui-monospace, Menlo, monospace", fontSize: 13, lineHeight: 1.5 }}>
        {rows.map((r) => (
          <div
            key={r.no}
            ref={r.no === target.start_line ? firstLineRef : undefined}
            style={{ display: "flex", background: r.hit ? "#fff6d5" : "transparent" }}
          >
            <span style={{ width: 48, flex: "none", textAlign: "right", paddingRight: 12, color: "#999", userSelect: "none" }}>
              {r.no}
            </span>
            <span style={{ whiteSpace: "pre" }}>{r.text}</span>
          </div>
        ))}
      </div>
    </Frame>
  );
}

function Frame({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section aria-label="관련 코드" style={{ border: "1px solid #ddd", borderRadius: 8, overflow: "hidden" }}>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid #eee", fontFamily: "ui-monospace, Menlo, monospace", fontSize: 13, background: "#fafafa" }}>
        {title}
      </div>
      {/* 불러오는 중, 실패 안내를 화면 낭독기가 읽도록 알림 영역으로 둡니다. */}
      <div style={{ padding: 12 }} aria-live="polite">
        {children}
      </div>
    </section>
  );
}
