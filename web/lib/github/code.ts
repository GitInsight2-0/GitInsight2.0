/**
 * 화면에 보여 줄 코드를 그때그때 받습니다 (README 설계 원칙 4: 원본 코드는 저장하지 않음).
 * 받은 코드는 이 탭의 메모리에만 잠깐 둡니다.
 *
 * 1. 서버 GET /api/code: 로그인 토큰으로 받아 비공개 레포도 됩니다 (#68, #95).
 *    레포와 커밋은 서버가 본인 분석 결과에서 정하고, 그 분석의 주제 파일만 줍니다.
 * 2. 서버가 못 주면 raw.githubusercontent.com: 공개 레포만 됩니다.
 *    DB가 없는 로컬과 실험 모드에서는 /api/code가 503·404라서 이쪽으로 받습니다.
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

/** 코드를 받을 곳. analysisId는 /api/code에, repo는 공개 레포 대비책에 씁니다 */
export interface CodeSource {
  analysisId: string | null;
  repo: string | null;
}

export function fetchFileLines(src: CodeSource, commitSha: string, path: string): Promise<string[]> {
  if (!SHA_PATTERN.test(commitSha)) return Promise.reject(new CodeFetchError("커밋 SHA 형식이 아닙니다"));
  const key = `${src.analysisId ?? ""}|${src.repo ?? ""}@${commitSha}:${path}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const p = (async () => {
    let serverError: CodeFetchError | null = null;
    if (src.analysisId) {
      try {
        return toLines(await fromServer(src.analysisId, path));
      } catch (e) {
        serverError = e instanceof CodeFetchError ? e : new CodeFetchError("서버에서 코드를 받지 못했습니다");
      }
    }
    if (src.repo && isValidRepo(src.repo)) {
      try {
        return toLines(await fromRaw(src.repo, commitSha, path));
      } catch (e) {
        // 서버가 사유를 알려 줬으면(파일이 너무 큼, 로그인 필요 등) 그쪽을 보여 줍니다.
        throw serverError ?? e;
      }
    }
    throw serverError ?? new CodeFetchError(src.repo ? `레포 이름 형식이 아닙니다: ${src.repo}` : "코드를 받을 레포를 모릅니다");
  })();
  // 실패한 요청은 캐시에 남기지 않아 다음 질문에서 다시 시도합니다.
  p.catch(() => cache.delete(key));
  cache.set(key, p);
  return p;
}

/** 서버 /api/code. 실패하면 서버가 준 사유를 담아 던집니다 */
async function fromServer(analysisId: string, path: string): Promise<string> {
  const q = new URLSearchParams({ analysis_id: analysisId, path });
  const r = await timedFetch(`/api/code?${q}`, { cache: "no-store" });
  if (r.ok) return r.text();
  const body = (await r.json().catch(() => null)) as { detail?: unknown } | null;
  const detail = typeof body?.detail === "string" ? body.detail : `서버 오류 (${r.status})`;
  throw new CodeFetchError(detail);
}

/** 공개 레포 대비책 (raw.githubusercontent.com) */
async function fromRaw(repo: string, commitSha: string, path: string): Promise<string> {
  const safePath = path
    .split("/")
    .filter((p) => p && p !== "." && p !== "..")
    .map(encodeURIComponent)
    .join("/");
  const r = await timedFetch(`https://raw.githubusercontent.com/${repo}/${commitSha}/${safePath}`, {});
  if (!r.ok) throw new CodeFetchError(`GitHub에서 코드를 받지 못했습니다 (${r.status})`);
  return r.text();
}

async function timedFetch(url: string, init: RequestInit): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: ctrl.signal });
  } catch {
    throw new CodeFetchError("코드를 받는 데 시간이 너무 오래 걸리거나 연결하지 못했습니다");
  } finally {
    clearTimeout(timer);
  }
}

/** CRLF 파일도 줄 끝에 \r이 남지 않게 나누고, 파일 끝 개행이 만드는 빈 줄은 뺍니다 (줄 수가 1 커지지 않게) */
function toLines(text: string): string[] {
  const lines = text.split(/\r?\n/);
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}
