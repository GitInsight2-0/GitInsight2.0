"use client";

import type { CompetencyScore, ProgressEntry, Report, SymbolRef, TopicReport, TurnRow } from "@gitinsight/contracts";
import { useEffect, useState } from "react";
import {
  ACTION_LABEL,
  COMPETENCY_LABEL,
  COMPETENCY_ORDER,
  FALLBACK_LABEL,
  INTENT_LABEL,
  MAX_LEVEL,
  type ReportLoad,
  fetchProgress,
  fetchReport,
  isGithubLink,
  previousEntry,
} from "@/lib/reports";
import styles from "./report.module.css";

type State = { kind: "loading" } | ReportLoad;

/**
 * 리포트 화면 (#20).
 * 서버가 준 리포트(계약 Report)를 그대로 보여 줍니다. 점수, 인용문, 코드 위치를 화면에서 만들거나 고치지 않습니다.
 */
export default function ReportScreen({ reportId }: { reportId: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  /** 지난 면접 기록. undefined는 아직 불러오는 중, null은 불러오지 못함(비교 칸을 뺍니다) */
  const [progress, setProgress] = useState<ProgressEntry[] | null | undefined>(undefined);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const ac = new AbortController();
    setState({ kind: "loading" });
    setProgress(undefined);
    fetchReport(reportId, ac.signal)
      .then(setState)
      .catch(() => undefined); // 화면을 떠나며 취소된 경우입니다.
    fetchProgress(ac.signal)
      .then(setProgress)
      .catch(() => undefined);
    return () => ac.abort();
  }, [reportId, attempt]);

  return (
    <div className={styles.page}>
      <header className={styles.topbar}>
        <a className={styles.brand} href="/">
          GitInsight
        </a>
        <a className={styles.ghost} href="/">
          첫 화면으로
        </a>
      </header>

      <main className={styles.main}>
        {state.kind === "loading" && (
          <p role="status" className={styles.muted}>
            리포트를 불러오고 있어요…
          </p>
        )}
        {state.kind === "login" && (
          <Problem text="로그인이 필요합니다. 로그인은 8시간 동안 유지돼요." linkText="첫 화면에서 다시 로그인" />
        )}
        {state.kind === "forbidden" && <Problem text="이 리포트는 열 수 없어요. 면접을 본 계정으로 로그인했는지 확인해 주세요." linkText="첫 화면으로" />}
        {state.kind === "missing" && <Problem text="리포트를 찾지 못했어요. 주소가 맞는지 확인해 주세요." linkText="첫 화면으로" />}
        {state.kind === "error" && (
          <div role="alert" className={styles.problem}>
            <p>리포트를 불러오지 못했어요. ({state.message})</p>
            <button type="button" className={styles.secondary} onClick={() => setAttempt((n) => n + 1)}>
              다시 불러오기
            </button>
          </div>
        )}
        {state.kind === "ok" && <ReportBody report={state.report} progress={progress} />}
      </main>
    </div>
  );
}

function Problem({ text, linkText }: { text: string; linkText: string }) {
  return (
    <div role="alert" className={styles.problem}>
      <p>{text}</p>
      <a className={styles.secondary} href="/">
        {linkText}
      </a>
    </div>
  );
}

function ReportBody({ report, progress }: { report: Report; progress: ProgressEntry[] | null | undefined }) {
  /** 턴 id → 몇 번째 답변인지 (1부터). 근거 인용문이 어느 답변에서 나왔는지 가리킵니다 */
  const turnNumber = new Map(report.turns.map((t, i) => [t.turn_id, i + 1]));
  const scored = [...report.competencies].sort(
    (a, b) => COMPETENCY_ORDER.indexOf(a.name) - COMPETENCY_ORDER.indexOf(b.name),
  );
  const scoredNames = new Set(scored.map((c) => c.name));
  const unscored = COMPETENCY_ORDER.filter((name) => !scoredNames.has(name));
  const previous = progress ? previousEntry(progress, report.session_id) : null;

  return (
    <>
      <section className={styles.head}>
        <h1>면접 리포트</h1>
        <p className={styles.muted}>
          답변 {report.turns.length}개, 주제 {report.topics.length}개를 바탕으로 만들었어요. 수준은 답변 내용만으로 매기고, 수준마다
          근거가 된 내 답변을 함께 보여 드려요.
        </p>
      </section>

      {report.kind === "turn_table" && (
        <p role="status" className={styles.notice}>
          이번에는 역량 평가를 만들지 못했어요. 주제별로 다룬 내용과 답변 기록만 보여 드려요.
        </p>
      )}

      {scored.length > 0 && (
        <section aria-labelledby="competencies" className={styles.section}>
          <h2 id="competencies">역량별 수준</h2>
          <p className={styles.muted}>수준은 1부터 {MAX_LEVEL}까지예요.</p>
          <ul className={styles.cards}>
            {scored.map((c) => (
              <CompetencyCard
                key={c.name}
                score={c}
                turnNumber={turnNumber}
                previousLevel={previous?.competencies.find((p) => p.name === c.name)?.level ?? null}
                compared={previous !== null}
              />
            ))}
          </ul>
          {unscored.length > 0 && (
            <p className={styles.muted}>
              근거가 될 답변이 부족해 평가하지 않은 역량: {unscored.map((n) => COMPETENCY_LABEL[n]).join(", ")}. 낮은 수준이라는
              뜻이 아니에요.
            </p>
          )}
          {progress !== undefined && progress !== null && previous === null && (
            <p className={styles.muted}>이전 면접 기록이 없어서 비교는 다음 면접부터 보여 드려요.</p>
          )}
        </section>
      )}

      {(report.strengths.length > 0 || report.improvements.length > 0 || report.next_topics.length > 0) && (
        <section aria-label="총평" className={styles.columns}>
          <Bullets title="잘한 점" items={report.strengths} />
          <Bullets title="보완할 점" items={report.improvements} />
          <Bullets title="다음에 준비할 주제" items={report.next_topics} />
        </section>
      )}

      {report.topics.length > 0 && (
        <section aria-labelledby="topics" className={styles.section}>
          <h2 id="topics">주제별 내용</h2>
          <ul className={styles.stack}>
            {report.topics.map((t, i) => (
              <TopicCard key={t.topic_id} topic={t} index={i + 1} />
            ))}
          </ul>
        </section>
      )}

      {report.turns.length > 0 && (
        <section aria-labelledby="turns" className={styles.section}>
          <h2 id="turns">답변 기록</h2>
          <ol className={styles.stack}>
            {report.turns.map((t, i) => (
              <TurnCard key={t.turn_id} turn={t} index={i + 1} />
            ))}
          </ol>
        </section>
      )}
    </>
  );
}

function CompetencyCard({
  score,
  turnNumber,
  previousLevel,
  compared,
}: {
  score: CompetencyScore;
  turnNumber: Map<string, number>;
  /** 바로 앞 면접에서의 수준. 그때 평가하지 않았으면 null */
  previousLevel: number | null;
  /** 바로 앞 면접 기록이 있는지 */
  compared: boolean;
}) {
  return (
    <li className={styles.card}>
      <div className={styles.cardHead}>
        <h3>{COMPETENCY_LABEL[score.name]}</h3>
        <LevelMeter level={score.level} />
      </div>
      {compared && <Change now={score.level} before={previousLevel} />}
      <ul className={styles.evidence}>
        {score.evidence.map((e, i) => {
          const n = turnNumber.get(e.turn_id);
          return (
            <li key={`${e.turn_id}-${i}`}>
              <blockquote>{e.quote}</blockquote>
              <div className={styles.source}>
                {n !== undefined && <a href={`#turn-${e.turn_id}`}>{n}번째 답변</a>}
                {e.symbol && <SymbolTag symbol={e.symbol} />}
              </div>
            </li>
          );
        })}
      </ul>
    </li>
  );
}

/** 수준을 칸 네 개로 보여 줍니다. 숫자도 같이 적습니다 */
function LevelMeter({ level }: { level: number }) {
  return (
    <div className={styles.level} role="img" aria-label={`수준 ${level}, 최고 ${MAX_LEVEL}`}>
      <span className={styles.levelBars} aria-hidden="true">
        {Array.from({ length: MAX_LEVEL }, (_, i) => (
          <span key={i} className={i < level ? styles.barOn : styles.barOff} />
        ))}
      </span>
      <span className={styles.levelText} aria-hidden="true">
        {level} / {MAX_LEVEL}
      </span>
    </div>
  );
}

/** 바로 앞 면접과의 차이 */
function Change({ now, before }: { now: number; before: number | null }) {
  if (before === null) return <p className={styles.change}>이전 면접에서는 평가하지 않은 역량이에요.</p>;
  const diff = now - before;
  if (diff === 0) return <p className={styles.change}>이전 면접과 같은 수준이에요 ({before}).</p>;
  return (
    <p className={diff > 0 ? styles.changeUp : styles.changeDown}>
      이전 면접 {before} → 이번 {now} ({diff > 0 ? `${diff}단계 올랐어요` : `${-diff}단계 내려갔어요`})
    </p>
  );
}

function SymbolTag({ symbol }: { symbol: SymbolRef }) {
  return (
    <span className={styles.symbol}>
      {symbol.path} {symbol.start_line}–{symbol.end_line}줄 · {symbol.name}
    </span>
  );
}

function Bullets({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className={styles.card}>
      <h2>{title}</h2>
      <ul className={styles.bullets}>
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function TopicCard({ topic, index }: { topic: TopicReport; index: number }) {
  return (
    <li className={styles.card}>
      <p className={styles.label}>주제 {index}</p>
      <h3>{topic.question}</h3>
      <div className={styles.points}>
        <PointList title="답변에서 다룬 내용" items={topic.covered} tone="on" empty="다룬 내용으로 확인된 것이 없어요." />
        <PointList title="다루지 않은 내용" items={topic.missing} tone="off" empty="빠진 내용이 없어요." />
      </div>
      {topic.model_answer && (
        <div className={styles.model}>
          <p className={styles.label}>모범 답변 예시</p>
          <p>{topic.model_answer}</p>
        </div>
      )}
      {topic.code_links.length > 0 && (
        <div>
          <p className={styles.label}>이 주제에서 본 코드</p>
          <ul className={styles.links}>
            {topic.code_links.map((link) => (
              <li key={link.symbol.symbol_id}>
                {isGithubLink(link.url) ? (
                  <a href={link.url} target="_blank" rel="noopener noreferrer">
                    <SymbolTag symbol={link.symbol} />
                    <span className={styles.out}>GitHub에서 열기</span>
                  </a>
                ) : (
                  <SymbolTag symbol={link.symbol} />
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

function PointList({ title, items, tone, empty }: { title: string; items: string[]; tone: "on" | "off"; empty: string }) {
  return (
    <div>
      <p className={styles.label}>{title}</p>
      {items.length === 0 ? (
        <p className={styles.muted}>{empty}</p>
      ) : (
        <ul className={tone === "on" ? styles.pointsOn : styles.pointsOff}>
          {items.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TurnCard({ turn, index }: { turn: TurnRow; index: number }) {
  return (
    <li id={`turn-${turn.turn_id}`} className={styles.turn}>
      <p className={styles.label}>{index}번째 답변</p>
      <p className={styles.turnQuestion}>{turn.question}</p>
      <p className={styles.turnAnswer}>{turn.transcript || "(받아 적은 답변이 없어요)"}</p>
      <p className={styles.turnMeta}>
        {turn.intent && <span>{INTENT_LABEL[turn.intent]}</span>}
        <span>다음: {ACTION_LABEL[turn.action]}</span>
        {turn.fallback_reason && <span>{FALLBACK_LABEL[turn.fallback_reason]}</span>}
      </p>
    </li>
  );
}
