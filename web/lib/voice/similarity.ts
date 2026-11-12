/**
 * 두 문장이 글자 단위로 얼마나 같은지 0~1로 돌려줍니다 (1 - 편집 거리 / 긴 쪽 길이).
 * 받아쓰기와 음성 AI의 도구 인자 transcript를 비교해, 모델이 답변을 다듬거나 요약했는지 보는 데 씁니다.
 * 공백과 문장부호는 빼고 비교합니다.
 */
const MAX_CHARS = 1000;

export function similarity(a: string, b: string): number {
  const x = normalize(a);
  const y = normalize(b);
  if (!x.length && !y.length) return 1;
  if (!x.length || !y.length) return 0;
  return Math.round((1 - editDistance(x, y) / Math.max(x.length, y.length)) * 100) / 100;
}

function normalize(s: string): string[] {
  return Array.from(s.toLowerCase().replace(/[\s.,!?·'"`~\-_()[\]{}]/g, "")).slice(0, MAX_CHARS);
}

function editDistance(a: string[], b: string[]): number {
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur.push(Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost));
    }
    prev = cur;
  }
  return prev[b.length];
}
