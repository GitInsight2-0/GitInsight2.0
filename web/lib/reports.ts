/** 리포트와 이전 면접 비교 불러오기 (계약 Report, ProgressEntry). 서버: GET /api/reports/{id}, GET /api/me/progress */
import type {
  Action,
  Competency,
  ExplainStatus,
  FallbackReason,
  Intent,
  ProgressEntry,
  Report,
} from "@gitinsight/contracts";

/** 리포트 화면 주소 */
export function reportHref(reportId: string): string {
  return `/reports/${encodeURIComponent(reportId)}`;
}

export type ReportLoad =
  | { kind: "ok"; report: Report }
  /** 로그인이 풀렸습니다 (401) */
  | { kind: "login" }
  /** 다른 사람의 리포트입니다 (403) */
  | { kind: "forbidden" }
  /** 없는 리포트입니다 (404). DB 없이 띄운 서버는 다시 시작하면 리포트가 사라집니다 */
  | { kind: "missing" }
  | { kind: "error"; message: string };

export async function fetchReport(reportId: string, signal?: AbortSignal): Promise<ReportLoad> {
  let res: Response;
  try {
    res = await fetch(`/api/reports/${encodeURIComponent(reportId)}`, { signal, cache: "no-store" });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    return { kind: "error", message: "서버에 연결할 수 없습니다." };
  }
  if (res.status === 401) return { kind: "login" };
  if (res.status === 403) return { kind: "forbidden" };
  // 리포트 id가 UUID 형식이 아니면 서버가 422로 답합니다. 없는 리포트와 같이 안내합니다.
  if (res.status === 404 || res.status === 422) return { kind: "missing" };
  if (!res.ok) return { kind: "error", message: `서버 오류 (${res.status})` };
  return { kind: "ok", report: (await res.json()) as Report };
}

/**
 * 지난 면접별 역량 수준 (오래된 면접부터).
 * 비교는 리포트의 덧붙임이라, 불러오지 못하면 null을 돌려주고 화면은 비교 칸만 뺍니다.
 */
export async function fetchProgress(signal?: AbortSignal): Promise<ProgressEntry[] | null> {
  try {
    const res = await fetch("/api/me/progress", { signal, cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as ProgressEntry[];
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    return null;
  }
}

/** 이 면접 바로 앞의 면접 기록. 이 면접이 목록에 없거나(역량 평가 없음) 첫 면접이면 null입니다 */
export function previousEntry(entries: ProgressEntry[], sessionId: string): ProgressEntry | null {
  const at = entries.findIndex((e) => e.session_id === sessionId);
  return at > 0 ? entries[at - 1] : null;
}

/** 역량 이름. docs/rubric.md의 제목과 같습니다 */
export const COMPETENCY_LABEL: Record<Competency, string> = {
  code_understanding: "코드 이해도",
  design_judgment: "설계 판단",
  problem_solving: "문제 해결·장애 대응",
  scalability_operations: "확장성·운영",
  communication: "커뮤니케이션",
  role_decision: "역할·의사결정 설명",
};

/** 화면에 보여 주는 순서 (docs/rubric.md의 순서) */
export const COMPETENCY_ORDER: Competency[] = [
  "code_understanding",
  "design_judgment",
  "problem_solving",
  "scalability_operations",
  "communication",
  "role_decision",
];

/** 수준의 최댓값 (계약 Level은 1~4) */
export const MAX_LEVEL = 4;

/** 답변 뒤에 면접관이 한 일 */
export const ACTION_LABEL: Record<Action, string> = {
  ASK: "질문",
  FOLLOW_UP: "꼬리질문",
  PROBE: "빠진 내용을 다시 질문",
  HINT: "힌트",
  NEXT_TOPIC: "다음 주제로",
  REPEAT: "질문을 다시 말함",
  CLARIFY: "질문 뜻을 설명",
  WRAP_UP: "면접 마무리",
};

/** 답변을 어떻게 받아들였는지 */
export const INTENT_LABEL: Record<Intent, string> = {
  ANSWER: "답변",
  DONT_KNOW: "모르겠다고 답함",
  CLARIFY: "질문 뜻을 되물음",
  REPEAT: "다시 말해 달라고 함",
  OFF_TOPIC: "질문과 다른 이야기",
};

/** 질문받은 함수를 얼마나 설명했는지 (코드 지도, #107) */
export const EXPLAIN_STATUS_LABEL: Record<ExplainStatus, string> = {
  explained: "설명함",
  explained_after_hint: "힌트 받고 설명함",
  stuck: "막힘",
};

/** 면접 중에 이 답변을 판단하지 못한 까닭 */
export const FALLBACK_LABEL: Record<FallbackReason, string> = {
  timeout: "면접 중에는 판단이 시간 안에 끝나지 않았어요",
  invalid: "면접 중에는 판단 결과를 쓸 수 없었어요",
  refusal: "면접 중에는 판단을 받지 못했어요",
  error: "면접 중에는 판단을 받지 못했어요",
  forced: "판단 없이 미리 준비한 질문으로 진행했어요",
};

/** 서버가 만든 GitHub 고정 링크만 엽니다 (https://github.com/{owner}/{repo}/blob/{sha}/{path}#L…) */
export function isGithubLink(url: string): boolean {
  return url.startsWith("https://github.com/");
}
