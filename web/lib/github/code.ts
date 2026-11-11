/**
 * 화면에 보여 줄 코드를 그때그때 GitHub에서 받습니다 (README 설계 원칙 4: 원본 코드는 저장하지 않음).
 * 받은 코드는 이 탭의 메모리에만 잠깐 둡니다.
 *
 * 지금은 공개 레포만 됩니다 (raw.githubusercontent.com).
 * 비공개 레포는 GitHub 로그인 연동(T4) 뒤 서버를 거쳐 받도록 바꿉니다.
 */

const REPO_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const SHA_PATTERN = /^[0-9a-f]{40}$/;
const FETCH_TIMEOUT_MS = 8000;

const cache = new Map<string, Promise<string[]>>();

export class CodeFetchError extends Error {}

export function fetchFileLines(repo: string, commitSha: string, path: string): Promise<string[]> {
  if (!REPO_PATTERN.test(repo)) return Promise.reject(new CodeFetchError(`레포 이름 형식이 아닙니다: ${repo}`));
  if (!SHA_PATTERN.test(commitSha)) return Promise.reject(new CodeFetchError("커밋 SHA 형식이 아닙니다"));
  const safePath = path
    .split("/")
    .filter((p) => p && p !== "." && p !== "..")
    .map(encodeURIComponent)
    .join("/");
  const url = `https://raw.githubusercontent.com/${repo}/${commitSha}/${safePath}`;

  const hit = cache.get(url);
  if (hit) return hit;
  const p = (async () => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    try {
      const r = await fetch(url, { signal: ctrl.signal });
      if (!r.ok) throw new CodeFetchError(`GitHub에서 코드를 받지 못했습니다 (${r.status})`);
      return (await r.text()).split("\n");
    } finally {
      clearTimeout(timer);
    }
  })();
  // 실패한 요청은 캐시에 남기지 않아 다음 질문에서 다시 시도합니다.
  p.catch(() => cache.delete(url));
  cache.set(url, p);
  return p;
}
