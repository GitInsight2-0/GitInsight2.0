"use client";

import type { Competency, ProgressEntry } from "@gitinsight/contracts";
import { useEffect, useRef, useState } from "react";
import { COMPETENCY_LABEL, COMPETENCY_ORDER, MAX_LEVEL } from "@/lib/reports";
import styles from "./report.module.css";

/** 그래프를 그리는 데 필요한 면접 수. 한 건이면 변화가 없어서 그리지 않습니다 */
export const MIN_GROWTH_ENTRIES = 2;

// SVG 좌표. 왼쪽은 세로축 숫자가 들어갈 자리입니다.
// 가로는 그래프가 놓인 칸의 실제 너비(px)를 재서 씁니다. 그래프를 늘려 그리면 그 안의 글자도 같이 커져서
// 화면의 다른 글자와 크기가 달라지기 때문입니다. DEFAULT_W는 너비를 재기 전의 값입니다.
const DEFAULT_W = 260;
const MIN_W = 160;
const H = 140;
const LEFT = 22;
const RIGHT = 14;
const TOP = 12;
const BOTTOM = 10;
const DOT_R = 4;
const NOW_R = 6;
const HIT_R = 12;

const DATE = new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric" });

function levelOf(entry: ProgressEntry, name: Competency): number | null {
  return entry.competencies.find((c) => c.name === name)?.level ?? null;
}

/**
 * 역량 변화 그래프 (#120).
 * 서버가 준 면접별 역량 수준(GET /api/me/progress, 오래된 것부터)을 그대로 그립니다. 수준과 날짜를 화면에서 만들지 않습니다.
 * 역량마다 작은 그래프를 하나씩 둡니다. 수준이 1~4의 정수라 한 그래프에 겹쳐 그리면 선이 포개져 읽을 수 없기 때문입니다.
 */
export default function GrowthChart({ entries, sessionId }: { entries: ProgressEntry[]; sessionId: string }) {
  if (entries.length < MIN_GROWTH_ENTRIES) return null;
  // 이 리포트의 면접. 목록에 없으면(역량 평가 없는 리포트) 가장 최근 면접을 기준으로 삼습니다.
  const found = entries.findIndex((e) => e.session_id === sessionId);
  const nowAt = found >= 0 ? found : entries.length - 1;
  const names = COMPETENCY_ORDER.filter((name) => entries.some((e) => levelOf(e, name) !== null));
  if (names.length === 0) return null;
  // 평가가 빠진 면접이 있는 역량이 있으면 회색 선의 뜻을 설명합니다.
  const hasGap = names.some((name) => entries.some((e) => levelOf(e, name) === null));

  return (
    <section aria-labelledby="growth" className={styles.section}>
      <h2 id="growth">역량 변화</h2>
      <p className={styles.muted}>
        역량 평가가 있는 최근 면접 {entries.length}건이에요. 가로는 면접 순서, 세로는 수준(1~{MAX_LEVEL})이에요.
        {found >= 0 ? " 큰 점이 이 리포트의 면접이에요." : ""}
        {hasGap ? " 회색 선은 그 역량을 평가하지 않은 면접이에요." : ""}
      </p>
      <ul className={styles.growthGrid}>
        {names.map((name) => (
          <GrowthTile key={name} name={name} entries={entries} nowAt={nowAt} marked={found >= 0} />
        ))}
      </ul>
      <details className={styles.growthTable}>
        <summary>표로 보기</summary>
        <div className={styles.tableWrap}>
          <table>
            <thead>
              <tr>
                <th scope="col">면접</th>
                {names.map((name) => (
                  <th key={name} scope="col">
                    {COMPETENCY_LABEL[name]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, i) => (
                <tr key={entry.session_id}>
                  <th scope="row">
                    {i + 1}번째 ({DATE.format(new Date(entry.date))}){found === i ? " · 이 리포트" : ""}
                  </th>
                  {names.map((name) => (
                    <td key={name}>{levelOf(entry, name) ?? "평가 없음"}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}

function GrowthTile({
  name,
  entries,
  nowAt,
  marked,
}: {
  name: Competency;
  entries: ProgressEntry[];
  nowAt: number;
  marked: boolean;
}) {
  const [active, setActive] = useState<number | null>(null);
  const plot = useRef<HTMLDivElement | null>(null);
  const [W, setW] = useState(DEFAULT_W);
  useEffect(() => {
    const el = plot.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setW(Math.max(MIN_W, Math.round(entry.contentRect.width))));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const levels = entries.map((e) => levelOf(e, name));
  const x = (i: number) => LEFT + (i * (W - LEFT - RIGHT)) / (entries.length - 1);
  const y = (level: number) => TOP + ((MAX_LEVEL - level) * (H - TOP - BOTTOM)) / (MAX_LEVEL - 1);

  // 평가가 없는 면접에서는 선을 끊습니다. 없는 값을 이어 그리지 않습니다.
  const runs: number[][] = [];
  levels.forEach((level, i) => {
    if (level === null) return;
    const last = runs[runs.length - 1];
    if (last && last[last.length - 1] === i - 1) last.push(i);
    else runs.push([i]);
  });

  const scored = levels.flatMap((level, i) => (level === null ? [] : [{ i, level }]));
  const first = scored[0];
  // 평가가 없던 구간: 앞뒤로 평가받은 두 면접을 회색 선으로 잇고, 그 사이 면접 자리를 가리킬 수 있게 합니다.
  // 회색 선 위의 높이는 값이 아닙니다. 그래서 점을 찍지 않고, 가리키면 "평가 없음"이라고 알려 줍니다.
  const gaps = scored.flatMap((to, k) => {
    const from = scored[k - 1];
    if (!from || to.i - from.i < 2) return [];
    const missing = Array.from({ length: to.i - from.i - 1 }, (_, n) => {
      const i = from.i + n + 1;
      const t = (i - from.i) / (to.i - from.i);
      return { i, cy: y(from.level) + (y(to.level) - y(from.level)) * t };
    });
    return [{ from, to, missing }];
  });
  // 맨 앞이나 맨 뒤의 면접에 평가가 없으면, 가장 가까운 평가의 높이에서 가장자리까지 회색 선을 긋습니다.
  // 이을 상대가 없어서 수평으로 긋습니다. 이 높이도 값이 아닙니다.
  const last = scored[scored.length - 1];
  if (first && first.i > 0) {
    gaps.push({
      from: { i: 0, level: first.level },
      to: first,
      missing: Array.from({ length: first.i }, (_, i) => ({ i, cy: y(first.level) })),
    });
  }
  if (last && last.i < entries.length - 1) {
    gaps.push({
      from: last,
      to: { i: entries.length - 1, level: last.level },
      missing: Array.from({ length: entries.length - 1 - last.i }, (_, n) => ({
        i: last.i + n + 1,
        cy: y(last.level),
      })),
    });
  }
  const now = levels[nowAt];
  const shown = active ?? nowAt;
  const shownLevel = levels[shown];

  return (
    <li className={styles.growthTile}>
      <div className={styles.growthHead}>
        <h3>{COMPETENCY_LABEL[name]}</h3>
        <p className={styles.growthValue}>
          {now === null ? "평가 없음" : `${now} / ${MAX_LEVEL}`}
        </p>
      </div>
      <p className={styles.muted}>{summary(first, now, nowAt)}</p>
      <div ref={plot} className={styles.growthPlot}>
      <svg
        className={styles.growthSvg}
        width={W}
        height={H}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`${COMPETENCY_LABEL[name]} 수준 변화. ${scored
          .map((p) => `${p.i + 1}번째 면접 수준 ${p.level}`)
          .join(", ")}`}
        onMouseLeave={() => setActive(null)}
      >
        {Array.from({ length: MAX_LEVEL }, (_, k) => k + 1).map((level) => (
          <line key={level} className={styles.growthRule} x1={LEFT} x2={W - RIGHT} y1={y(level)} y2={y(level)} />
        ))}
        <text className={styles.growthTick} x={LEFT - 8} y={y(MAX_LEVEL)} dominantBaseline="middle" textAnchor="end">
          {MAX_LEVEL}
        </text>
        <text className={styles.growthTick} x={LEFT - 8} y={y(1)} dominantBaseline="middle" textAnchor="end">
          1
        </text>
        {gaps.map((gap) => (
          <line
            key={`${gap.from.i}-${gap.to.i}`}
            className={styles.growthGap}
            x1={x(gap.from.i)}
            y1={y(gap.from.level)}
            x2={x(gap.to.i)}
            y2={y(gap.to.level)}
          />
        ))}
        {runs
          .filter((run) => run.length > 1)
          .map((run) => (
            <polyline
              key={run[0]}
              className={styles.growthLine}
              points={run.map((i) => `${x(i)},${y(levels[i] as number)}`).join(" ")}
            />
          ))}
        {scored.map(({ i, level }) => (
          <circle
            key={i}
            className={styles.growthDot}
            cx={x(i)}
            cy={y(level)}
            r={marked && i === nowAt ? NOW_R : DOT_R}
          />
        ))}
        {gaps.flatMap((gap) =>
          gap.missing.map(({ i, cy }) => (
            <circle
              key={`gap-${i}`}
              className={styles.growthHit}
              cx={x(i)}
              cy={cy}
              r={HIT_R}
              tabIndex={0}
              aria-label={missingText(entries[i], i)}
              onMouseEnter={() => setActive(i)}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
            />
          )),
        )}
        {/* 점보다 넓은 영역으로 가리킬 수 있게 합니다. 키보드로도 옮겨 다닐 수 있습니다. */}
        {scored.map(({ i, level }) => (
          <circle
            key={i}
            className={styles.growthHit}
            cx={x(i)}
            cy={y(level)}
            r={HIT_R}
            tabIndex={0}
            aria-label={pointText(entries[i], i, level)}
            onMouseEnter={() => setActive(i)}
            onFocus={() => setActive(i)}
            onBlur={() => setActive(null)}
          />
        ))}
      </svg>
      </div>
      <p className={styles.growthReadout} aria-live="polite">
        {shownLevel === null ? missingText(entries[shown], shown) : pointText(entries[shown], shown, shownLevel)}
      </p>
    </li>
  );
}

function pointText(entry: ProgressEntry, index: number, level: number): string {
  return `${index + 1}번째 면접 (${DATE.format(new Date(entry.date))}) · 수준 ${level}`;
}

function missingText(entry: ProgressEntry, index: number): string {
  return `${index + 1}번째 면접 (${DATE.format(new Date(entry.date))}) · 평가 없음`;
}

/** 처음 평가받은 면접과 이 면접의 차이를 한 문장으로 */
function summary(first: { i: number; level: number } | undefined, now: number | null, nowAt: number): string {
  if (first === undefined) return "아직 평가받은 면접이 없어요.";
  if (now === null) return "이 면접에서는 평가하지 않은 역량이에요.";
  if (first.i === nowAt) return "이 면접에서 처음 평가받은 역량이에요.";
  const diff = now - first.level;
  if (diff === 0) return `처음 평가받은 면접과 같은 수준이에요 (${first.level}).`;
  return `처음 ${first.level} → 이 면접 ${now} (${diff > 0 ? `${diff}단계 올랐어요` : `${-diff}단계 내려갔어요`})`;
}
