/** 브라우저 오류를 사람이 바로 이해할 수 있는 문구로 바꿉니다. */
import { ApiError } from "./api";

/** 로그인이 풀려서 난 오류인지 (로그인 쿠키는 최대 8시간) */
export function isLoginError(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401;
}

export function explainError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401) return "로그인이 필요합니다. 첫 화면에서 GitHub로 다시 로그인해 주세요.";
    if (e.status === 404) return "면접할 분석 결과를 찾지 못했습니다. 첫 화면에서 다시 시작해 주세요.";
    return e.message;
  }
  const name = (e as { name?: unknown } | null)?.name;
  if (name === "NotAllowedError") {
    return "마이크 권한이 막혀 있습니다. Chrome에서 이 페이지를 열고 주소창 왼쪽 아이콘에서 마이크를 허용하세요. (Claude 앱 안의 브라우저는 마이크를 쓸 수 없습니다)";
  }
  if (name === "NotFoundError") return "마이크를 찾을 수 없습니다. 이어폰이나 마이크 연결을 확인하세요.";
  return String(e);
}
