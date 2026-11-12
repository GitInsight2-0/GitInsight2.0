/**
 * 측정값 요약. 분위수는 선형 보간(R type 7, 엑셀 PERCENTILE.INC와 같음)으로 계산합니다.
 * 표본이 적으면 p90, p95가 최댓값에 가까워지므로 n을 같이 봅니다.
 */
export interface Summary {
  n: number;
  median: number | null;
  p90: number | null;
  p95: number | null;
  max: number | null;
}

export function quantile(sorted: readonly number[], q: number): number | null {
  if (!sorted.length) return null;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  const v = sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  return Math.round(v);
}

export function summarize(xs: readonly number[]): Summary {
  const s = [...xs].sort((a, b) => a - b);
  return {
    n: s.length,
    median: quantile(s, 0.5),
    p90: quantile(s, 0.9),
    p95: quantile(s, 0.95),
    max: s.length ? s[s.length - 1] : null,
  };
}
