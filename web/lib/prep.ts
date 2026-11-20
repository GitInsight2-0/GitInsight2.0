/**
 * 면접관 질문지 (#173). 지원자의 공개 레포 주소로 분석을 요청하고, 끝나면 분석 결과로 질문지를 보여 줍니다.
 * 서버: POST /api/analyses/public (계약 PublicAnalysisCreate), GET /api/analyses/{id}/result (계약 AnalysisResult)
 */
import type { AnalysisCreated, AnalysisResult, PublicAnalysisCreate } from "@gitinsight/contracts";
import { ApiError } from "@/lib/voice/api";

/** 공개 레포 분석을 시작합니다. 같은 레포·커밋의 끝난 분석이 있으면 서버가 그것을 돌려줍니다 (cached) */
export async function startPublicAnalysis(repoUrl: string, signal?: AbortSignal): Promise<AnalysisCreated> {
  const body: PublicAnalysisCreate = { repo_url: repoUrl };
  const res = await fetch("/api/analyses/public", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new ApiError(res.status, await detailOf(res));
  return (await res.json()) as AnalysisCreated;
}

/** 요청자 본인의 완료된 분석 결과. 없거나 다른 사람의 분석이거나 끝나지 않았으면 404입니다 */
export async function fetchAnalysisResult(analysisId: string, signal?: AbortSignal): Promise<AnalysisResult> {
  const res = await fetch(`/api/analyses/${encodeURIComponent(analysisId)}/result`, { signal, cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, await detailOf(res));
  return (await res.json()) as AnalysisResult;
}

/** 질문지 화면 주소 */
export function prepHref(analysisId: string): string {
  return `/prep/${encodeURIComponent(analysisId)}`;
}

/**
 * 분석한 커밋에 고정된 GitHub 코드 주소. 공개 레포라 면접관이 로그인 없이 열 수 있습니다.
 * 경로와 줄 번호는 분석 결과 그대로 씁니다.
 */
export function githubLineUrl(repo: string, commit: string, path: string, start: number, end: number): string {
  const file = path.split("/").map(encodeURIComponent).join("/");
  const lines = start === end ? `L${start}` : `L${start}-L${end}`;
  return `https://github.com/${repo}/blob/${commit}/${file}#${lines}`;
}

async function detailOf(res: Response): Promise<string> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  return typeof body?.detail === "string" ? body.detail : `서버 오류 (${res.status})`;
}
