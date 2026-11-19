"use client";

import type { LineSpan, SymbolRef } from "@gitinsight/contracts";
import { useEffect, useRef, useState } from "react";
import { fetchFileLines } from "@/lib/github/code";
import styles from "./interview.module.css";

/** 강조 범위 앞뒤로 보여 줄 줄 수 */
const CONTEXT_LINES = 4;
/**
 * 강조 범위가 이보다 길면 정의 줄만 진하게, 몸통은 옅게 칠합니다 (#157).
 * 서버는 심볼 전체를 주므로 긴 함수는 100줄 넘게 덮여 어디를 보라는 건지 알기 어렵습니다.
 */
const LONG_RANGE = 15;

const KIND_LABEL: Record<SymbolRef["kind"], string> = {
  function: "함수",
  method: "메서드",
  class: "클래스",
};

/** 강조 범위의 줄 수 */
const rangeLength = (t: SymbolRef) => t.end_line - t.start_line + 1;

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  /** key는 이 내용이 어느 파일의 것인지입니다. 파일이 바뀐 첫 렌더에서 옛 내용을 새 줄 범위로 그리지 않게 비교합니다. */
  | { kind: "ok"; key: string; lines: string[] }
  | { kind: "error"; message: string };

/**
 * 서버가 준 highlight(경로, 줄 범위, 커밋)만 강조합니다. 줄 번호를 화면에서 바꾸지 않습니다.
 * focus가 있으면 그 줄만 진하게 칠하고 그 줄로 스크롤합니다 (#156). 나머지 심볼은 옅게 칠합니다.
 * focus가 highlight 안이라는 것은 계약과 서버가 보장합니다.
 * 코드를 못 받으면 위치만 보여 줍니다 (런북 "코드 뷰어" 1차 대비책).
 */
export default function CodeViewer({
  analysisId,
  repo,
  target,
  focus = null,
}: {
  /** 서버 /api/code에 넘길 분석 id (비공개 레포도 됨) */
  analysisId: string | null;
  /** 공개 레포 대비책(raw.githubusercontent.com)에 쓸 레포 이름 */
  repo: string | null;
  target: SymbolRef | null;
  /** target 안에서 지금 질문과 관계있는 줄. 없으면 null */
  focus?: LineSpan | null;
}) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const firstLineRef = useRef<HTMLDivElement | null>(null);
  const key = target ? `${analysisId}|${repo}@${target.commit_sha}:${target.path}` : "";

  useEffect(() => {
    if (!target || (!repo && !analysisId)) {
      setState({ kind: "idle" });
      return;
    }
    let alive = true;
    setState({ kind: "loading" });
    fetchFileLines({ analysisId, repo }, target.commit_sha, target.path)
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
  }, [state, target?.start_line, focus?.start_line]);

  if (!target) {
    return (
      <Panel target={null} status="강조할 코드가 없습니다">
        <p className={styles.codeMessage}>이 질문에는 강조할 코드가 없습니다.</p>
      </Panel>
    );
  }
  if ((!repo && !analysisId) || state.kind === "error") {
    return (
      <Panel target={target} status="코드를 불러오지 못해 위치만 보여 줍니다">
        <p className={styles.codeMessage}>
          코드를 불러오지 못해 위치만 보여 줍니다{state.kind === "error" ? ` (${state.message})` : ""}.
        </p>
      </Panel>
    );
  }
  if (state.kind !== "ok" || state.key !== key) {
    return (
      <Panel target={target} status="코드를 불러오는 중">
        <p className={styles.codeMessage}>코드를 불러오는 중…</p>
      </Panel>
    );
  }

  const from = Math.max(1, target.start_line - CONTEXT_LINES);
  const to = Math.min(state.lines.length, target.end_line + CONTEXT_LINES);
  const long = rangeLength(target) > LONG_RANGE;
  // focus가 있으면 그 줄이 진하게 칠할 곳이고, 처음 보여 줄 곳입니다.
  const strong = (no: number) =>
    focus ? no >= focus.start_line && no <= focus.end_line : !long || no === target.start_line;
  const scrollTo = focus?.start_line ?? target.start_line;
  /** head: 진하게 칠할 줄, body: 범위 안의 나머지(옅게), null: 범위 밖 */
  const rows: { no: number; text: string; hit: "head" | "body" | null }[] = [];
  for (let no = from; no <= to; no++) {
    const inRange = no >= target.start_line && no <= target.end_line;
    rows.push({
      no,
      text: state.lines[no - 1] ?? "",
      hit: !inRange ? null : strong(no) ? "head" : "body",
    });
  }
  const outOfRange = target.end_line > state.lines.length;

  return (
    <Panel
      target={target}
      focus={focus}
      status={
        focus
          ? `${target.path} ${focus.start_line}–${focus.end_line}줄을 강조한 코드를 불러왔습니다`
          : `${target.path} ${target.start_line}–${target.end_line}줄 코드를 불러왔습니다`
      }
    >
      {outOfRange && (
        <p className={styles.codeWarn}>
          파일이 {state.lines.length}줄이라 강조 범위({target.start_line}-{target.end_line})와 맞지 않습니다. 분석 커밋을 확인하세요.
        </p>
      )}
      <div ref={scrollRef} className={styles.codeBox} tabIndex={0} aria-label={`${target.path} 코드`}>
        {rows.map((r) => (
          <div
            key={r.no}
            ref={r.no === scrollTo ? firstLineRef : undefined}
            className={
              r.hit === "head"
                ? `${styles.row} ${styles.rowHit}`
                : r.hit === "body"
                  ? `${styles.row} ${styles.rowBody}`
                  : styles.row
            }
          >
            <span className={styles.lineNo}>{r.no}</span>
            <span className={styles.lineText}>{r.text}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function Panel({
  target,
  focus = null,
  status,
  children,
}: {
  target: SymbolRef | null;
  focus?: LineSpan | null;
  status: string;
  children: React.ReactNode;
}) {
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
            <span className={styles.codeSpan}>
              {KIND_LABEL[target.kind]} 전체 {rangeLength(target)}줄
            </span>
            {focus && (
              <span className={styles.codeFocus}>
                볼 곳 {focus.start_line}–{focus.end_line}줄
              </span>
            )}
          </div>
        )}
      </div>
      {/* 알림 영역에는 짧은 상태 문장만 둡니다. 코드 본문까지 넣으면 화면 낭독기가 코드를 다 읽습니다. */}
      <span role="status" aria-live="polite" className={styles.srOnly}>
        {status}
      </span>
      <div className={styles.codeBody}>{children}</div>
    </aside>
  );
}
