"use client";

import { useEffect, useState } from "react";
import { LOGIN_ERROR_TEXT, LOGIN_URL, type MeState, fetchMe, logout } from "@/lib/auth";
import { DEMO_ANALYSIS_ID, DEMO_REPO } from "@/lib/demo";
import styles from "./home.module.css";

/**
 * 첫 화면. 로그인 전에는 소개와 GitHub 로그인, 로그인 뒤에는 면접 시작을 보여 줍니다.
 * 레포 목록과 선택(#18 2단계)이 붙기 전까지는 데모 분석으로 면접을 시작합니다.
 */
export default function HomeScreen() {
  const [me, setMe] = useState<MeState>({ kind: "loading" });
  const [loginError, setLoginError] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    // 로그인 실패로 돌아오면 서버가 ?login_error=를 붙입니다. 안내만 남기고 주소에서는 지웁니다 (새로고침해도 다시 뜨지 않게).
    const url = new URL(window.location.href);
    const code = url.searchParams.get("login_error");
    if (code) {
      setLoginError(LOGIN_ERROR_TEXT[code] ?? "로그인하지 못했어요. 다시 시도해 주세요.");
      url.searchParams.delete("login_error");
      window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    }
    const ac = new AbortController();
    void load(ac.signal);
    return () => ac.abort();
  }, []);

  async function load(signal?: AbortSignal) {
    setMe({ kind: "loading" });
    try {
      setMe(await fetchMe(signal));
    } catch {
      // 화면을 떠나며 취소된 경우입니다.
    }
  }

  async function signOut() {
    setLeaving(true);
    await logout();
    setLeaving(false);
    await load();
  }

  return (
    <div className={styles.page}>
      <header className={styles.topbar}>
        <span className={styles.brand}>GitInsight</span>
        {me.kind === "in" && (
          <div className={styles.user}>
            {me.me.avatar_url && <img className={styles.avatar} src={me.me.avatar_url} alt="" width={32} height={32} />}
            <span className={styles.login}>{me.me.login}</span>
            <button type="button" className={styles.ghost} onClick={() => void signOut()} disabled={leaving}>
              로그아웃
            </button>
          </div>
        )}
      </header>

      <main className={styles.main}>
        <section className={styles.hero}>
          <h1>내가 쓴 코드로 보는 AI 기술면접</h1>
          <p>
            GitHub 레포와 커밋 이력을 분석해서, 실제로 작업한 코드를 화면에 띄워 두고 음성으로 질문합니다. 답변에 따라 꼬리질문이
            이어지고, 끝나면 답변과 코드가 연결된 리포트를 받습니다.
          </p>
        </section>

        {loginError && (
          <p role="alert" className={styles.notice}>
            {loginError}
          </p>
        )}

        <section aria-live="polite" className={styles.panel}>
          {me.kind === "loading" && <p className={styles.muted}>로그인 상태를 확인하고 있어요…</p>}

          {me.kind === "out" && (
            <>
              <a className={styles.primary} href={LOGIN_URL}>
                GitHub로 로그인
              </a>
              <p className={styles.muted}>레포는 읽기 권한으로만 봅니다. 로그인은 8시간 동안 유지돼요.</p>
            </>
          )}

          {me.kind === "in" && (
            <>
              <div className={styles.repo}>
                <span className={styles.label}>면접 볼 레포</span>
                <span className={styles.repoName}>{DEMO_REPO}</span>
                <span className={styles.muted}>레포 선택이 열리기 전까지는 준비된 데모 레포로 면접을 봅니다.</span>
              </div>
              <a className={styles.primary} href={`/interview/${encodeURIComponent(DEMO_ANALYSIS_ID)}`}>
                면접 시작
              </a>
              <p className={styles.muted}>음성으로 답하려면 Chrome에서 이어폰을 끼고 시작하세요.</p>
            </>
          )}

          {me.kind === "error" && (
            <>
              <p className={styles.error}>{me.message}</p>
              <button type="button" className={styles.secondary} onClick={() => void load()}>
                다시 확인
              </button>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
