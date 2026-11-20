"use client";

import { useEffect, useState } from "react";
import { LOGIN_ERROR_TEXT, LOGIN_URL, type MeState, fetchMe, logout } from "@/lib/auth";
import Logo from "./Logo";
import RepoPicker, { Steps } from "./RepoPicker";
import styles from "./home.module.css";

/**
 * 첫 화면. 로그인 전에는 소개와 GitHub 로그인, 로그인 뒤에는 면접 시작을 보여 줍니다.
 * 로그인 뒤 레포 고르기와 분석 진행은 RepoPicker가 맡습니다.
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
        <span className={styles.brand}>
          <Logo size={24} />
          GitInsight
        </span>
        {me.kind === "in" && (
          <div className={styles.user}>
            {me.me.avatar_url && <img className={styles.avatar} src={me.me.avatar_url} alt="" width={28} height={28} />}
            <span className={styles.login}>{me.me.login}</span>
            <button type="button" className={styles.ghost} onClick={() => void signOut()} disabled={leaving}>
              로그아웃
            </button>
          </div>
        )}
      </header>

      <main className={styles.main}>
        <section className={styles.hero}>
          <Logo size={64} className={styles.heroMark} />
          <h1>GitInsight</h1>
          <p className={styles.tagline}>내가 쓴 코드로 보는 AI 기술면접</p>
          <p className={styles.lead}>
            GitHub 레포와 커밋 이력을 분석해서, 실제로 작업한 코드를 화면에 띄워 두고 질문합니다. 답변에 따라 꼬리질문이
            이어지고, 끝나면 답변과 코드가 연결된 리포트를 받습니다.
          </p>
        </section>

        {loginError && (
          <p role="alert" className={styles.notice}>
            {loginError}
          </p>
        )}

        <section aria-live="polite" className={styles.card}>
          {me.kind === "loading" && <p className={styles.muted}>로그인 상태를 확인하고 있어요…</p>}

          {me.kind === "out" && (
            <>
              <Steps at={0} />
              <h2 className={styles.cardTitle}>내가 작업한 코드로 면접을 봐요</h2>
              <p className={styles.muted}>
                GitHub를 연결하면 앱을 설치한 레포만 읽기 전용으로 가져와요. 비공개 레포도 돼요. 분석이 끝난 원본 코드는 저장하지
                않아요.
              </p>
              <a className={styles.primary} href={LOGIN_URL}>
                <GitHubIcon />
                GitHub로 연결하기
              </a>
              <p className={styles.muted}>로그인은 8시간 동안 유지돼요.</p>
            </>
          )}

          {me.kind === "in" && (
            <>
              <p className={styles.connected}>
                <span aria-hidden="true">✓</span> GitHub 연결됨 · {me.me.login}
              </p>
              <RepoPicker />
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

        {/* 면접관 입구 (#173). 지원자의 공개 레포로 질문지를 만듭니다 */}
        <p className={styles.small}>
          면접관이신가요?{" "}
          <a className={styles.textLink} href="/prep">
            지원자의 공개 레포로 면접 질문지 만들기
          </a>
        </p>
      </main>
    </div>
  );
}

function GitHubIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38v-1.33c-2.23.48-2.7-1.07-2.7-1.07-.36-.92-.89-1.17-.89-1.17-.73-.5.06-.49.06-.49.8.06 1.23.83 1.23.83.72 1.22 1.88.87 2.34.66.07-.52.28-.87.5-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48v2.2c0 .21.15.46.55.38A8 8 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}
