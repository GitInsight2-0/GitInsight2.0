"use client";

import type { AnalysisResult, SymbolRef, Topic } from "@gitinsight/contracts";
import { useEffect, useState } from "react";
import { LOGIN_URL } from "@/lib/auth";
import { fetchAnalysisResult, githubLineUrl } from "@/lib/prep";
import { ApiError } from "@/lib/voice/api";
import Logo from "./Logo";
import styles from "./prep.module.css";

type Load =
  | { kind: "loading" }
  | { kind: "ok"; result: AnalysisResult }
  | { kind: "error"; message: string; needLogin: boolean };

const DIFFICULTY_LABEL: Record<number, string> = {
  1: "흐름 이해",
  2: "설계 이유",
  3: "예외·부하",
};

const KIND_LABEL: Record<SymbolRef["kind"], string> = {
  function: "함수",
  method: "메서드",
  class: "클래스",
};

/**
 * 면접관 질문지 (#173). 분석 결과(AnalysisResult)를 주제별로 보여 주고 인쇄할 수 있게 합니다.
 * 질문, 요점, 꼬리질문은 분석이 만든 그대로이고, 코드 위치와 줄 번호도 분석 결과 그대로 GitHub 주소로 엽니다.
 */
export default function PrepSheet({ analysisId }: { analysisId: string }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });

  useEffect(() => {
    const ac = new AbortController();
    fetchAnalysisResult(analysisId, ac.signal)
      .then((result) => setLoad({ kind: "ok", result }))
      .catch((err: unknown) => {
        if (ac.signal.aborted) return;
        if (err instanceof ApiError && err.status === 401) {
          setLoad({ kind: "error", message: "로그인이 필요해요.", needLogin: true });
        } else if (err instanceof ApiError && err.status === 404) {
          setLoad({ kind: "error", message: "질문지를 찾지 못했어요. 분석이 아직 끝나지 않았거나 다른 계정의 분석이에요.", needLogin: false });
        } else {
          setLoad({ kind: "error", message: "질문지를 불러오지 못했어요. 잠시 뒤 다시 시도해 주세요.", needLogin: false });
        }
      });
    return () => ac.abort();
  }, [analysisId]);

  return (
    <div className={styles.page}>
      <header className={styles.topbar}>
        <a className={styles.brand} href="/">
          <Logo size={22} />
          GitInsight
        </a>
        <div className={styles.topActions}>
          <a className={styles.ghost} href="/prep">
            다른 레포
          </a>
          {load.kind === "ok" && (
            <button type="button" className={styles.ghost} onClick={() => window.print()}>
              인쇄
            </button>
          )}
        </div>
      </header>

      <main className={styles.sheet}>
        {load.kind === "loading" && <p className={styles.muted}>질문지를 불러오고 있어요…</p>}
        {load.kind === "error" && (
          <p role="alert" className={styles.error}>
            {load.message}{" "}
            {load.needLogin && (
              <a className={styles.link} href={LOGIN_URL}>
                GitHub로 로그인
              </a>
            )}
          </p>
        )}
        {load.kind === "ok" && <Sheet result={load.result} />}
      </main>
    </div>
  );
}

function Sheet({ result }: { result: AnalysisResult }) {
  const repo = result.repo_full_name;
  const commit = result.commit_sha;
  return (
    <>
      <section className={styles.head}>
        <p className={styles.eyebrow}>면접 질문지</p>
        <h1>{repo}</h1>
        <p className={styles.meta}>
          커밋{" "}
          <a className={styles.link} href={`https://github.com/${repo}/tree/${commit}`} target="_blank" rel="noopener noreferrer">
            <code>{commit.slice(0, 7)}</code>
          </a>{" "}
          · 주제 {result.topics.length}개 · 함수와 클래스 {result.symbol_count}개 분석
        </p>
        <p className={styles.note}>
          질문과 요점은 AI가 코드를 읽고 만든 초안이에요. 면접 전에 코드 링크로 한 번 확인해 주세요. 커밋 이력으로 누가 쓴 코드인지는
          가리지 않았어요.
        </p>
      </section>

      {result.topics.length === 0 ? (
        <p className={styles.muted}>질문을 만들 만한 코드를 찾지 못했어요.</p>
      ) : (
        <ol className={styles.topics}>
          {result.topics.map((topic, i) => (
            <TopicCard key={topic.topic_id} topic={topic} index={i + 1} repo={repo} commit={commit} />
          ))}
        </ol>
      )}
    </>
  );
}

function TopicCard({ topic, index, repo, commit }: { topic: Topic; index: number; repo: string; commit: string }) {
  const s = topic.symbol;
  const symbolOf = new Map(topic.related_symbols.map((r) => [r.symbol_id, r]));
  const at = (ref: { path: string; start_line: number; end_line: number }) =>
    githubLineUrl(repo, commit, ref.path, ref.start_line, ref.end_line);
  return (
    <li className={styles.topic}>
      <header className={styles.topicHead}>
        <span className={styles.topicNo}>{index}</span>
        <div>
          <h2>{topic.title}</h2>
          <p className={styles.where}>
            <a className={styles.link} href={at(s)} target="_blank" rel="noopener noreferrer">
              {s.path}:{s.start_line}–{s.end_line}
            </a>{" "}
            · {KIND_LABEL[s.kind]} <code>{s.name}</code> · {DIFFICULTY_LABEL[topic.difficulty] ?? `난이도 ${topic.difficulty}`}
          </p>
        </div>
      </header>

      <p className={styles.summary}>{topic.summary}</p>

      <div className={styles.block}>
        <h3>질문</h3>
        <p className={styles.question}>{topic.base_question}</p>
        <p className={styles.muted}>의도: {topic.question_intent}</p>
      </div>

      <div className={styles.block}>
        <h3>좋은 답이 다뤄야 할 요점</h3>
        <ol className={styles.points}>
          {topic.expected_points.map((p) => (
            <li key={p.point_id}>
              <p>{p.text}</p>
              <p className={styles.probe}>
                빠지면: {p.probe_question}
                {p.focus && (
                  <>
                    {" "}
                    <a className={styles.link} href={at({ path: s.path, ...p.focus })} target="_blank" rel="noopener noreferrer">
                      {p.focus.start_line}–{p.focus.end_line}줄
                    </a>
                  </>
                )}
              </p>
            </li>
          ))}
        </ol>
      </div>

      {topic.prepared_followups.length > 0 && (
        <div className={styles.block}>
          <h3>꼬리질문</h3>
          <ul className={styles.followups}>
            {topic.prepared_followups.map((f, i) => {
              const ref = f.symbol_id ? symbolOf.get(f.symbol_id) : undefined;
              return (
                <li key={i}>
                  {f.question}
                  {ref && (
                    <>
                      {" "}
                      <a className={styles.link} href={at(ref)} target="_blank" rel="noopener noreferrer">
                        <code>{ref.name}</code>
                      </a>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <div className={styles.block}>
        <h3>막히면 줄 힌트</h3>
        <p>{topic.hint}</p>
      </div>

      {topic.misstatement && (
        <div className={styles.block}>
          <h3>확인용으로 일부러 틀린 말 (선택)</h3>
          <p>
            “{topic.misstatement.statement}” <span className={styles.muted}>→ 짚지 못하면: {topic.misstatement.correction}</span>
          </p>
        </div>
      )}
    </li>
  );
}
