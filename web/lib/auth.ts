/** 로그인 상태 확인과 로그아웃 (서버 #55의 /api/me, /api/auth/*) */
import type { Me } from "@gitinsight/contracts";

/** 로그인 시작 주소. fetch가 아니라 페이지 이동으로 엽니다 (GitHub 인증 화면으로 넘어가야 해서) */
export const LOGIN_URL = "/api/auth/github/login";

export type MeState =
  | { kind: "loading" }
  | { kind: "in"; me: Me }
  | { kind: "out" }
  /** 서버에 닿지 못함 (서버가 꺼졌거나 로그인 설정이 빠짐) */
  | { kind: "error"; message: string };

/** 로그인한 사용자를 확인합니다. 401이면 로그인 전입니다 */
export async function fetchMe(signal?: AbortSignal): Promise<MeState> {
  let res: Response;
  try {
    res = await fetch("/api/me", { signal, cache: "no-store" });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    return { kind: "error", message: "서버에 연결할 수 없습니다." };
  }
  if (res.status === 401) return { kind: "out" };
  if (!res.ok) return { kind: "error", message: `서버 오류 (${res.status})` };
  return { kind: "in", me: (await res.json()) as Me };
}

export async function logout(): Promise<void> {
  // 실패해도 로그인 쿠키는 최대 8시간 뒤 만료됩니다. 화면은 다시 /api/me로 상태를 확인합니다.
  await fetch("/api/auth/logout", { method: "POST" }).catch(() => undefined);
}

/** 서버가 로그인 실패 때 붙여 보내는 ?login_error= 값의 안내 문구 */
export const LOGIN_ERROR_TEXT: Record<string, string> = {
  denied: "GitHub 로그인을 취소했어요. 다시 시도하려면 아래 버튼을 눌러 주세요.",
  invalid_state: "로그인 요청이 만료됐거나 맞지 않아요. 다시 로그인해 주세요.",
  github: "GitHub와 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요.",
};
