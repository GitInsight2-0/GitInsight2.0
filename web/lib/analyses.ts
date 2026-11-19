/** 레포 목록과 분석 요청·진행 (계약 Repo, AnalysisCreate, AnalysisCreated, AnalysisState) */
import type { AnalysisCreated, AnalysisState, AnalysisStatus, Repo } from "@gitinsight/contracts";
import { ApiError } from "@/lib/voice/api";

export type ReposResult =
  | { kind: "ok"; repos: Repo[] }
  /** 서버에 아직 레포 목록 API가 없음 (2단계 전). 데모 레포로 시작합니다 */
  | { kind: "missing" }
  | { kind: "error"; status: number; message: string };

export async function fetchRepos(signal?: AbortSignal): Promise<ReposResult> {
  const res = await fetch("/api/repos", { signal, cache: "no-store" });
  if (res.status === 404 || res.status === 405 || res.status === 501) return { kind: "missing" };
  if (!res.ok) return { kind: "error", status: res.status, message: await detailOf(res) };
  return { kind: "ok", repos: (await res.json()) as Repo[] };
}

/** 분석을 시작합니다. 같은 레포·커밋의 끝난 분석이 있으면 서버가 그것을 돌려줍니다 (cached) */
export async function startAnalysis(repoId: number, signal?: AbortSignal): Promise<AnalysisCreated> {
  const res = await fetch("/api/analyses", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ repo_id: repoId }),
    signal,
  });
  if (!res.ok) throw new ApiError(res.status, await detailOf(res));
  return (await res.json()) as AnalysisCreated;
}

export async function fetchAnalysis(analysisId: string, signal?: AbortSignal): Promise<AnalysisState> {
  const res = await fetch(`/api/analyses/${encodeURIComponent(analysisId)}`, { signal, cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, await detailOf(res));
  return (await res.json()) as AnalysisState;
}

/**
 * 진행 상태 조회를 다시 해 볼 오류인지 (#102).
 * 중계·서버의 일시 오류(502, 503, 504)와 네트워크 오류만 다시 묻습니다.
 * 401(로그인 만료), 404(없는 분석) 같은 나머지는 다시 물어도 같아서 바로 멈춥니다.
 */
export function isRetryablePollError(e: unknown): boolean {
  if (e instanceof ApiError) return e.status === 502 || e.status === 503 || e.status === 504;
  // fetch는 연결이 끊기면 TypeError를 던집니다.
  return e instanceof TypeError;
}

export const STATUS_TEXT: Record<AnalysisStatus, string> = {
  queued: "분석을 기다리고 있어요",
  stage1: "코드 구조와 커밋 이력을 읽고 있어요",
  stage2: "면접 주제를 만들고 있어요",
  done: "분석이 끝났어요",
  failed: "분석하지 못했어요",
};

/** 지원하는 언어 안내. 워커는 지금 Python 심볼만 뽑습니다 (#9). 다른 언어는 해커톤 뒤 (#72) */
export const SUPPORTED_LANGUAGE_NOTE = "지금은 Python 코드가 있는 레포만 분석할 수 있어요.";

/**
 * Python 코드가 없어 분석하지 못했을 때 AnalysisState.error에 오는 예외 이름 (#70, #71).
 * 서버는 예외 종류 이름만 보내므로 이 이름으로 문구를 고릅니다. 워커의 이름이 바뀌면 여기도 바꿉니다.
 */
export const UNSUPPORTED_CODE_ERROR = "NoSupportedCodeError";

/** 분석 실패 사유가 "지원하지 않는 언어"인지 */
export function isUnsupportedCode(error: string | null | undefined): boolean {
  return error === UNSUPPORTED_CODE_ERROR;
}

/**
 * 면접 화면 주소. 코드 뷰어가 코드를 받을 레포 이름을 같이 넘깁니다.
 * review를 주면 지난 면접에서 답변이 부족했던 주제만 묻는 다시 보기 면접입니다 (#107).
 */
export function interviewHref(analysisId: string, repo?: string, opts?: { review?: boolean }): string {
  const q = new URLSearchParams();
  if (repo) q.set("repo", repo);
  if (opts?.review) q.set("review", "1");
  const query = q.toString();
  return `/interview/${encodeURIComponent(analysisId)}${query ? `?${query}` : ""}`;
}

async function detailOf(res: Response): Promise<string> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  return typeof body?.detail === "string" ? body.detail : `서버 오류 (${res.status})`;
}
