"use client";

import type {
  CatchStatus,
  CodeMap,
  CompetencyScore,
  ExplainStatus,
  MisstatementResult,
  ProgressEntry,
  Report,
  SymbolRef,
  TopicReport,
  TurnRow,
} from "@gitinsight/contracts";
import { type ReactNode, useEffect, useState } from "react";
import { interviewHref } from "@/lib/analyses";
import GrowthChart, { MIN_GROWTH_ENTRIES } from "./GrowthChart";
import {
  ACTION_LABEL,
  CATCH_STATUS_LABEL,
  COMPETENCY_LABEL,
  COMPETENCY_ORDER,
  EXPLAIN_STATUS_LABEL,
  FALLBACK_LABEL,
  INTENT_LABEL,
  MAX_LEVEL,
  type ReportLoad,
  fetchProgress,
  fetchReport,
  isGithubLink,
  previousEntry,
} from "@/lib/reports";
import Logo from "./Logo";
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
          <Logo size={22} />
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
  const hinted = afterHintTurnIds(report.turns);
  const codeMap = report.code_map;

  // 근거 인용문의 "n번째 답변" 링크는 접힌 답변 기록 안을 가리킵니다. 가리키는 곳이 접혀 있으면 펼칩니다 (#132).
  useEffect(() => {
    const open = (id: string) => {
      const el = id ? document.getElementById(id) : null;
      if (!el) return;
      for (let d = el.closest("details"); d; d = d.parentElement?.closest("details") ?? null) d.open = true;
      el.scrollIntoView({ block: "start" });
    };
    // `#%`처럼 깨진 주소는 풀다가 예외가 납니다. 그때는 아무것도 펼치지 않습니다 (#166).
    const decode = (raw: string) => {
      try {
        return decodeURIComponent(raw);
      } catch {
        return "";
      }
    };
    const fromHash = () => open(decode(window.location.hash.slice(1)));
    // 같은 링크를 다시 누르면 주소의 #이 그대로라 hashchange가 오지 않습니다. 누를 때 바로 펼칩니다 (#155 리뷰).
    const onClick = (e: MouseEvent) => {
      const a = (e.target as Element | null)?.closest?.('a[href^="#"]');
      if (!a) return;
      const id = decode((a.getAttribute("href") ?? "").slice(1));
      // 기본 동작(주소의 # 바꾸기)이 끝난 뒤 펼치고 이동합니다.
      setTimeout(() => open(id), 0);
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    document.addEventListener("click", onClick);
    return () => {
      window.removeEventListener("hashchange", fromHash);
      document.removeEventListener("click", onClick);
    };
  }, []);

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

      {codeMap && canReviewAgain(codeMap) && <ReviewAgain codeMap={codeMap} />}

      <div className={styles.folds}>
        <p className={styles.foldsLead}>자세한 내용은 눌러서 펼쳐 보세요.</p>

        {progress && hasGrowth(progress) && (
          <Fold title="역량 변화" hint={`면접 ${progress.length}번`}>
            <GrowthChart entries={progress} sessionId={report.session_id} />
          </Fold>
        )}

        {codeMap && (
          <Fold
            title="코드 지도"
            hint={
              codeMap.key_function_count > 0
                ? `핵심 함수 ${codeMap.key_function_count}개 중 ${codeMap.explained_count + codeMap.after_hint_count}개 설명`
                : `질문받은 함수 ${codeMap.entries.length}개`
            }
          >
            <CodeMapSection codeMap={codeMap} topics={report.topics} />
          </Fold>
        )}

        {report.misstatement && (
          <Fold
            title="일부러 틀린 말 확인"
            hint={report.misstatement.status ? CATCH_STATUS_LABEL[report.misstatement.status] : "판단 없음"}
          >
            <MisstatementSection result={report.misstatement} turnNumber={turnNumber} />
          </Fold>
        )}

        {report.topics.length > 0 && (
          <Fold title="주제별 내용" hint={`주제 ${report.topics.length}개`}>
            <ul className={styles.stack}>
              {report.topics.map((t, i) => (
                <TopicCard key={t.topic_id} topic={t} index={i + 1} stuck={stuckQuestions(report.turns, t.topic_id)} />
              ))}
            </ul>
          </Fold>
        )}

        {report.turns.length > 0 && (
          <Fold title="답변 기록" hint={`답변 ${report.turns.length}개`}>
            <ol className={styles.stack}>
              {report.turns.map((t, i) => (
                <TurnCard
                  key={t.turn_id}
                  turn={t}
                  index={i + 1}
                  afterHint={hinted.has(t.turn_id)}
                  misstatement={isMisstatementTurn(t, report.misstatement) ? report.misstatement : null}
                />
              ))}
            </ol>
          </Fold>
        )}
      </div>
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
      {/* 근거가 없으면 빈 칸이 펼쳐지지 않게 접기를 두지 않습니다 (#155 리뷰) */}
      {score.evidence.length > 0 && (
      <details className={styles.evidenceFold}>
        <summary>근거가 된 답변 {score.evidence.length}개 보기</summary>
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
      </details>
      )}
    </li>
  );
}

/**
 * 접어 두는 세부 내용 (#132). 제목 옆에 한 줄 요약을 보여 주고, 누르면 펼칩니다.
 * 안쪽 구역의 제목(h2)은 이 제목과 겹쳐서 CSS로 감춥니다 (aria-labelledby로는 그대로 쓰입니다).
 */
function Fold({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <details className={styles.fold}>
      <summary>
        <span className={styles.foldTitle}>{title}</span>
        <span className={styles.foldHint}>{hint}</span>
        <span className={styles.foldArrow} aria-hidden="true" />
      </summary>
      <div className={styles.foldBody}>{children}</div>
    </details>
  );
}

/** 역량 변화 그래프를 그릴 만한 기록이 있는지 (GrowthChart가 아무것도 그리지 않을 때 빈 접기를 두지 않게) */
function hasGrowth(entries: ProgressEntry[]): boolean {
  return entries.length >= MIN_GROWTH_ENTRIES && entries.some((e) => e.competencies.length > 0);
}

/** 다시 볼 질문이 있는지. 지난 면접에서 부족했던 함수가 남아 있어도 다시 볼 수 있습니다 */
function canReviewAgain(codeMap: CodeMap): boolean {
  return codeMap.entries.some((e) => e.status !== "explained") || codeMap.stuck_count + codeMap.after_hint_count > 0;
}

/** 다시 보기 면접 버튼. 코드 지도를 접어 두어도 보이게 총평 아래에 둡니다 */
function ReviewAgain({ codeMap }: { codeMap: CodeMap }) {
  return (
    <div className={styles.mapAgain}>
      <a className={styles.secondary} href={interviewHref(codeMap.analysis_id, codeMap.repo_full_name, { review: true })}>
        부족했던 질문만 다시 면접 보기
      </a>
      <p className={styles.muted}>
        이 레포로 본 면접에서 막혔거나 힌트를 받고 답한 질문만 다시 물어봐요. 힌트 없이 설명하면 다음 다시 보기에서 빠져요.
      </p>
    </div>
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

const STATUS_CLASS: Record<ExplainStatus, string> = {
  explained: styles.statusExplained,
  explained_after_hint: styles.statusAfterHint,
  stuck: styles.statusStuck,
};

/**
 * 설명 못 한 코드 지도 (#107).
 * 상태와 숫자는 서버가 턴 기록으로 정한 값입니다. 화면에서는 더하기만 합니다.
 */
function CodeMapSection({ codeMap, topics }: { codeMap: CodeMap; topics: TopicReport[] }) {
  // 다시 볼 질문: 이번 면접에서 막혔거나 힌트를 받고 설명한 함수입니다. 질문 문장은 그 주제의 질문에서 가져옵니다.
  const questionOf = new Map(topics.map((t) => [t.topic_id, t.question]));
  const weak = codeMap.entries.filter((entry) => entry.status !== "explained");
  const total = codeMap.key_function_count;
  const explained = codeMap.explained_count + codeMap.after_hint_count;
  const notAsked = Math.max(0, total - explained - codeMap.stuck_count);
  const bar: { key: string; count: number; className: string }[] = [
    { key: "explained", count: codeMap.explained_count, className: styles.mapExplained },
    { key: "after_hint", count: codeMap.after_hint_count, className: styles.mapAfterHint },
    { key: "stuck", count: codeMap.stuck_count, className: styles.mapStuck },
    { key: "not_asked", count: notAsked, className: styles.mapNotAsked },
  ];

  return (
    <section aria-labelledby="code-map" className={styles.section}>
      <h2 id="code-map">코드 지도</h2>
      {total > 0 && (
        <div className={styles.card}>
          <p className={styles.mapHeadline}>
            핵심 함수 {total}개 중 <strong>{explained}개</strong>를 설명했어요.
          </p>
          <div className={styles.mapBar} aria-hidden="true">
            {bar
              .filter((part) => part.count > 0)
              .map((part) => (
                <span key={part.key} className={part.className} style={{ flexGrow: part.count }} />
              ))}
          </div>
          <ul className={styles.mapLegend}>
            <li>
              <span className={styles.mapExplained} aria-hidden="true" />
              {EXPLAIN_STATUS_LABEL.explained} {codeMap.explained_count}개
            </li>
            <li>
              <span className={styles.mapAfterHint} aria-hidden="true" />
              {EXPLAIN_STATUS_LABEL.explained_after_hint} {codeMap.after_hint_count}개
            </li>
            <li>
              <span className={styles.mapStuck} aria-hidden="true" />
              {EXPLAIN_STATUS_LABEL.stuck} {codeMap.stuck_count}개
            </li>
            <li>
              <span className={styles.mapNotAsked} aria-hidden="true" />
              아직 묻지 않음 {notAsked}개
            </li>
          </ul>
          <p className={styles.muted}>
            이 레포로 본 면접을 모두 합친 숫자예요. 여러 번 질문받은 함수는 가장 최근 면접의 상태로 세요.
          </p>
        </div>
      )}

      <div>
        <p className={styles.label}>이번 면접에서 질문받은 함수</p>
        {codeMap.entries.length === 0 ? (
          <p className={styles.muted}>이번 면접에서는 설명했는지 확인할 수 있는 답변이 없었어요.</p>
        ) : (
          <ul className={styles.mapEntries}>
            {codeMap.entries.map((entry) => (
              <li key={entry.symbol.symbol_id}>
                <span className={STATUS_CLASS[entry.status]}>{EXPLAIN_STATUS_LABEL[entry.status]}</span>
                <SymbolTag symbol={entry.symbol} />
                {isGithubLink(entry.url) && (
                  <a className={styles.out} href={entry.url} target="_blank" rel="noopener noreferrer">
                    GitHub에서 열기
                  </a>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <p className={styles.label}>답변이 부족했던 질문</p>
        {weak.length === 0 ? (
          <p className={styles.muted}>이번 면접에서는 막히거나 힌트를 받은 질문이 없었어요.</p>
        ) : (
          <ul className={styles.mapEntries}>
            {weak.map((entry) => (
              <li key={entry.symbol.symbol_id}>
                <span className={STATUS_CLASS[entry.status]}>{EXPLAIN_STATUS_LABEL[entry.status]}</span>
                <span className={styles.mapQuestion}>{questionOf.get(entry.topic_id) ?? entry.symbol.name}</span>
                <SymbolTag symbol={entry.symbol} />
              </li>
            ))}
          </ul>
        )}
      </div>

    </section>
  );
}

const CATCH_CLASS: Record<CatchStatus, string> = {
  caught: styles.statusExplained,
  partial: styles.statusAfterHint,
  missed: styles.statusStuck,
};

/** 면접관이 일부러 한 틀린 말에 답한 턴인지. 다시 말해 달라고 한 턴도 같은 문장을 질문으로 가집니다 */
function isMisstatementTurn(turn: TurnRow, result: MisstatementResult | null): boolean {
  if (!result) return false;
  return turn.turn_id === result.turn_id || (turn.topic_id === result.topic_id && turn.question === result.statement);
}

/**
 * 면접관이 일부러 한 틀린 말과 그 결과 (#133).
 * 틀린 말, 정답, 짚었는지는 서버가 준 값을 그대로 보여 줍니다. 짚지 못했어도 정답은 늘 보여 줍니다.
 */
function MisstatementSection({
  result,
  turnNumber,
}: {
  result: MisstatementResult;
  turnNumber: Map<string, number>;
}) {
  const n = result.turn_id ? turnNumber.get(result.turn_id) : undefined;
  return (
    <section aria-labelledby="misstatement" className={styles.section}>
      <h2 id="misstatement">일부러 틀린 말 확인</h2>
      <p className={styles.muted}>
        면접관이 확인을 위해 코드에 대해 일부러 틀린 말을 한 번 했어요. 이 결과는 역량 수준에 반영하지 않아요.
      </p>
      <div className={styles.card}>
        <div className={styles.misHead}>
          {result.status ? (
            <span className={CATCH_CLASS[result.status]}>{CATCH_STATUS_LABEL[result.status]}</span>
          ) : (
            <span className={styles.statusAfterHint}>
              {result.turn_id ? "짚었는지 판단하지 못했어요" : "답하기 전에 면접이 끝났어요"}
            </span>
          )}
        </div>
        <div>
          <p className={styles.label}>면접관이 한 틀린 말</p>
          <p className={styles.misWrong}>{result.statement}</p>
        </div>
        <div className={styles.model}>
          <p className={styles.label}>정답</p>
          <p>{result.correction}</p>
        </div>
        {result.quote && (
          <ul className={styles.evidence}>
            <li>
              <blockquote>{result.quote}</blockquote>
              <div className={styles.source}>
                {n !== undefined && <a href={`#turn-${result.turn_id}`}>{n}번째 답변</a>}
              </div>
            </li>
          </ul>
        )}
        {!result.quote && n !== undefined && (
          <p className={styles.source}>
            <a href={`#turn-${result.turn_id}`}>{n}번째 답변</a>
          </p>
        )}
      </div>
    </section>
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

/** 모르겠다고 답한 질문 */
interface StuckQuestion {
  turnId: string;
  /** 몇 번째 답변인지 (1부터) */
  number: number;
  question: string;
  /** 힌트를 받고 같은 주제에서 다시 답했는지 */
  retried: boolean;
}

/**
 * 그 주제에서 모르겠다고 답한 질문입니다.
 * "기대한 요점"은 주제의 첫 질문에 대한 것이라, 꼬리질문에 답하지 못한 것은 거기에 나오지 않습니다. 그래서 따로 보여 줍니다.
 */
function stuckQuestions(turns: TurnRow[], topicId: string): StuckQuestion[] {
  const stuck: StuckQuestion[] = [];
  turns.forEach((turn, i) => {
    if (turn.topic_id !== topicId || turn.intent !== "DONT_KNOW") return;
    const next = turns[i + 1];
    const retried = turn.action === "HINT" && next?.topic_id === topicId && next.intent === "ANSWER";
    stuck.push({ turnId: turn.turn_id, number: i + 1, question: turn.question, retried });
  });
  return stuck;
}

/** 힌트를 받은 뒤에 한 답변 (바로 앞 답변 뒤에 면접관이 같은 주제로 힌트를 준 경우) */
function afterHintTurnIds(turns: TurnRow[]): Set<string> {
  const ids = new Set<string>();
  turns.forEach((turn, i) => {
    const before = turns[i - 1];
    if (before && before.action === "HINT" && before.topic_id === turn.topic_id) ids.add(turn.turn_id);
  });
  return ids;
}

function TopicCard({ topic, index, stuck }: { topic: TopicReport; index: number; stuck: StuckQuestion[] }) {
  return (
    <li className={styles.card}>
      <p className={styles.label}>주제 {index}</p>
      <h3>{topic.question}</h3>
      <div className={styles.points}>
        <PointList
          title="기대한 요점 중 답변에서 다룬 것"
          items={topic.covered}
          tone="on"
          empty="다룬 것으로 확인된 요점이 없어요."
        />
        <PointList
          title="기대한 요점 중 다루지 않은 것"
          items={topic.missing}
          tone="off"
          empty="기대한 요점은 모두 다뤘어요."
        />
      </div>
      {stuck.length > 0 && (
        <div>
          <p className={styles.label}>모르겠다고 답한 질문</p>
          <ul className={styles.stuck}>
            {stuck.map((q) => (
              <li key={q.turnId}>
                <span>{q.question}</span>
                <span className={styles.source}>
                  <a href={`#turn-${q.turnId}`}>{q.number}번째 답변</a>
                  <span className={styles.stuckNote}>
                    {q.retried ? "힌트를 받고 다시 답했어요" : "답하지 못하고 넘어갔어요"}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
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

function TurnCard({
  turn,
  index,
  afterHint,
  misstatement,
}: {
  turn: TurnRow;
  index: number;
  afterHint: boolean;
  /** 이 답변이 받은 질문이 면접관이 일부러 한 틀린 말이면 그 결과 (#133) */
  misstatement: MisstatementResult | null;
}) {
  return (
    <li id={`turn-${turn.turn_id}`} className={styles.turn}>
      <p className={styles.label}>{index}번째 답변</p>
      {misstatement && (
        <p className={styles.misNote}>
          면접관이 일부러 한 틀린 말이에요. 정답: {misstatement.correction}
        </p>
      )}
      <p className={styles.turnQuestion}>{turn.question}</p>
      <p className={styles.turnAnswer}>{turn.transcript || "(받아 적은 답변이 없어요)"}</p>
      <p className={styles.turnMeta}>
        {afterHint && <span>힌트를 받은 뒤의 답변</span>}
        {turn.intent && <span>{INTENT_LABEL[turn.intent]}</span>}
        <span>다음: {ACTION_LABEL[turn.action]}</span>
        {turn.fallback_reason && <span>{FALLBACK_LABEL[turn.fallback_reason]}</span>}
      </p>
    </li>
  );
}
