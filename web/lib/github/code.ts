/**
 * 화면에 보여 줄 코드를 그때그때 GitHub에서 받습니다 (README 설계 원칙 4: 원본 코드는 저장하지 않음).
 * 받은 코드는 이 탭의 메모리에만 잠깐 둡니다.
 *
 * 지금은 공개 레포만 됩니다 (raw.githubusercontent.com).
 * 비공개 레포는 GitHub 로그인 연동(T4) 뒤 서버를 거쳐 받도록 바꿉니다.
 */

/** GitHub owner 이름에는 영문, 숫자, 하이픈만 씁니다. */
const OWNER_PATTERN = /^[A-Za-z0-9-]+$/;
/** 레포 이름에는 영문, 숫자, `.`, `_`, `-`를 쓰지만 `.`, `..`만으로 된 이름은 경로 이동이 되므로 막습니다. */
const REPO_NAME_PATTERN = /^[A-Za-z0-9_.-]+$/;

export function isValidRepo(repo: string): boolean {
  const parts = repo.split("/");
  if (parts.length !== 2) return false;
  const [owner, name] = parts;
  return OWNER_PATTERN.test(owner) && REPO_NAME_PATTERN.test(name) && name !== "." && name !== "..";
}
const SHA_PATTERN = /^[0-9a-f]{40}$/;
const FETCH_TIMEOUT_MS = 8000;

const cache = new Map<string, Promise<string[]>>();

export class CodeFetchError extends Error {}

export function fetchFileLines(repo: string, commitSha: string, path: string): Promise<string[]> {
  if (!isValidRepo(repo)) return Promise.reject(new CodeFetchError(`레포 이름 형식이 아닙니다: ${repo}`));
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
      // CRLF 파일도 줄 끝에 \r이 남지 않게 나누고, 파일 끝 개행이 만드는 빈 줄은 뺍니다 (줄 수가 1 커지지 않게).
      const lines = (await r.text()).split(/\r?\n/);
      if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
      return lines;
    } finally {
      clearTimeout(timer);
    }
  })();
  // 실패한 요청은 캐시에 남기지 않아 다음 질문에서 다시 시도합니다.
  p.catch(() => cache.delete(url));
  cache.set(url, p);
  return p;
}
