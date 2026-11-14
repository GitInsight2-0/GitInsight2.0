"use client";

import type { SymbolRef } from "@gitinsight/contracts";
import { useEffect, useRef, useState } from "react";
import { fetchFileLines } from "@/lib/github/code";
import styles from "./interview.module.css";

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
    return (
      <Panel target={null}>
        <p className={styles.codeMessage}>이 질문에는 강조할 코드가 없습니다.</p>
      </Panel>
    );
  }
  if (!repo || state.kind === "error") {
    return (
      <Panel target={target}>
        <p className={styles.codeMessage}>
          코드를 불러오지 못해 위치만 보여 줍니다{state.kind === "error" ? ` (${state.message})` : ""}.
        </p>
      </Panel>
    );
  }
  if (state.kind !== "ok" || state.key !== key) {
    return (
      <Panel target={target}>
        <p className={styles.codeMessage}>코드를 불러오는 중…</p>
      </Panel>
    );
  }

  const from = Math.max(1, target.start_line - CONTEXT_LINES);
  const to = Math.min(state.lines.length, target.end_line + CONTEXT_LINES);
  const rows: { no: number; text: string; hit: boolean }[] = [];
  for (let no = from; no <= to; no++) {
    rows.push({ no, text: state.lines[no - 1] ?? "", hit: no >= target.start_line && no <= target.end_line });
  }
  const outOfRange = target.end_line > state.lines.length;

  return (
    <Panel target={target}>
      {outOfRange && (
        <p className={styles.codeWarn}>
          파일이 {state.lines.length}줄이라 강조 범위({target.start_line}-{target.end_line})와 맞지 않습니다. 분석 커밋을 확인하세요.
        </p>
      )}
      <div ref={scrollRef} className={styles.codeBox} tabIndex={0} aria-label={`${target.path} 코드`}>
        {rows.map((r) => (
          <div
            key={r.no}
            ref={r.no === target.start_line ? firstLineRef : undefined}
            className={r.hit ? `${styles.row} ${styles.rowHit}` : styles.row}
          >
            <span className={styles.lineNo}>{r.no}</span>
            <span className={styles.lineText}>{r.text}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function Panel({ target, children }: { target: SymbolRef | null; children: React.ReactNode }) {
  return (
    <aside aria-label="질문이 가리키는 코드" className={styles.code}>
      <div className={styles.codeHead}>
        <div className={styles.label}>질문이 가리키는 코드</div>
        {target && (
          <div className={styles.codeWhere}>
            <span className={styles.codePath}>{target.path}</span>
            <span className={styles.codeRange}>
              {target.start_line}–{target.end_line}줄
            </span>
            <span className={styles.codeName}>{target.name}</span>
          </div>
        )}
      </div>
      {/* 불러오는 중, 실패 안내를 화면 낭독기가 읽도록 알림 영역으로 둡니다. */}
      <div aria-live="polite" className={styles.codeBody}>
        {children}
      </div>
    </aside>
  );
}
