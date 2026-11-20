"use client";

import type { AnalysisState } from "@gitinsight/contracts";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { STATUS_TEXT, SUPPORTED_LANGUAGE_NOTE, fetchAnalysis, isRetryablePollError, isUnsupportedCode } from "@/lib/analyses";
import { LOGIN_URL, type MeState, fetchMe } from "@/lib/auth";
import { prepHref, startPublicAnalysis } from "@/lib/prep";
import { ApiError } from "@/lib/voice/api";
import Logo from "./Logo";
import styles from "./home.module.css";
import prep from "./prep.module.css";

/** 분석 진행을 다시 묻는 간격 */
const POLL_MS = 2000;
/** 진행 상태 조회가 잇달아 실패해도 다시 묻는 횟수 (RepoPicker와 같음, #102) */
const POLL_RETRIES = 5;

type Job =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "running"; analysisId: string; state: AnalysisState | null }
  | { kind: "error"; message: string; needLogin: boolean };

/**
 * 면접관 입구 (#173). 지원자의 공개 레포 주소를 받아 분석하고, 끝나면 질문지 화면으로 넘어갑니다.
 * 비공개 레포는 서버가 받지 않습니다. 지원자 동의를 받았는지 화면에서 확인받습니다.
 */
export default function PrepStart() {
  const [me, setMe] = useState<MeState>({ kind: "loading" });
  const [url, setUrl] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [job, setJob] = useState<Job>({ kind: "idle" });
  const jobAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    fetchMe(ac.signal)
      .then(setMe)
      .catch(() => undefined);
    return () => {
      ac.abort();
      jobAbort.current?.abort();
    };
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!url.trim() || !agreed) return;
    jobAbort.current?.abort();
    const ac = new AbortController();
    jobAbort.current = ac;
    setJob({ kind: "starting" });
    let analysisId: string;
    let state: AnalysisState | null = null;
    try {
      const created = await startPublicAnalysis(url.trim(), ac.signal);
      analysisId = created.analysis_id;
      if (created.status === "done") state = { status: "done", progress: 1, commit_sha: null, error: null };
    } catch (err) {
      if (!ac.signal.aborted) setJob(failure(err));
      return;
    }
    let failures = 0;
    setJob({ kind: "running", analysisId, state });
    while (state?.status !== "done" && state?.status !== "failed") {
      try {
        await sleep(POLL_MS, ac.signal);
        state = await fetchAnalysis(analysisId, ac.signal);
        failures = 0;
        setJob({ kind: "running", analysisId, state });
      } catch (err) {
        if (ac.signal.aborted) return;
        failures += 1;
        if (!isRetryablePollError(err) || failures > POLL_RETRIES) {
          setJob(failure(err));
          return;
        }
      }
    }
    if (state.status === "done") window.location.assign(prepHref(analysisId));
  }

  function cancel() {
    jobAbort.current?.abort();
    setJob({ kind: "idle" });
  }

  const busy = job.kind === "starting" || job.kind === "running";

  return (
    <div className={styles.page}>
      <header className={styles.topbar}>
        <a className={`${styles.brand} ${prep.homeLink}`} href="/">
          <Logo size={24} />
          GitInsight
        </a>
        {me.kind === "in" && <span className={styles.login}>{me.me.login}</span>}
      </header>

      <main className={styles.main}>
        <section className={styles.hero}>
          <p className={styles.tagline}>면접관용 질문지</p>
          <h1 className={prep.title}>지원자 레포로 면접 질문을 준비해요</h1>
          <p className={styles.lead}>
            지원자의 공개 GitHub 레포 주소를 넣으면 코드를 분석해 주제별 질문, 좋은 답의 요점, 꼬리질문, 코드 위치를 정리해
            드려요.
          </p>
        </section>

        <section aria-live="polite" className={styles.card}>
          {me.kind === "loading" && <p className={styles.muted}>로그인 상태를 확인하고 있어요…</p>}

          {me.kind === "out" && (
            <>
              <h2 className={styles.cardTitle}>먼저 GitHub로 로그인해 주세요</h2>
              <p className={styles.muted}>분석 비용 때문에 로그인한 사용자만 쓸 수 있어요. 지원자 레포에 앱을 설치할 필요는 없어요.</p>
              <a className={styles.primary} href={LOGIN_URL}>
                GitHub로 로그인
              </a>
            </>
          )}

          {me.kind === "error" && <p className={styles.error}>{me.message}</p>}

          {me.kind === "in" && (
            <form className={prep.form} onSubmit={(e) => void submit(e)}>
              <label className={prep.field}>
                <span className={styles.label}>지원자의 공개 레포 주소</span>
                <input
                  className={styles.search}
                  type="text"
                  inputMode="url"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="https://github.com/owner/repo"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  disabled={busy}
                  maxLength={300}
                  required
                />
              </label>
              <p className={styles.small}>
                공개 레포만 분석해요. {SUPPORTED_LANGUAGE_NOTE} 기본 브랜치의 최신 커밋을 분석하고, 분석이 끝난 원본 코드는 저장하지
                않아요.
              </p>
              <label className={prep.agree}>
                <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} disabled={busy} />
                <span>
                  지원자에게 이 레포를 면접 준비에 쓴다고 알리고 동의를 받았어요. 분석하는 동안 코드 일부가 AI(Google Gemini)로
                  보내져요.
                </span>
              </label>
              <div className={styles.actions}>
                <button type="submit" className={styles.primary} disabled={busy || !agreed || !url.trim()}>
                  질문지 만들기
                </button>
                {busy && (
                  <button type="button" className={styles.secondary} onClick={cancel}>
                    그만 기다리기
                  </button>
                )}
              </div>
              <JobStatus job={job} />
            </form>
          )}
        </section>
      </main>
    </div>
  );
}

function JobStatus({ job }: { job: Job }) {
  if (job.kind === "starting") return <p className={styles.muted}>레포를 확인하고 있어요…</p>;
  if (job.kind === "running") {
    const state = job.state;
    if (state?.status === "failed") {
      return (
        <p className={styles.error}>
          {isUnsupportedCode(state.error) ? `분석할 코드를 찾지 못했어요. ${SUPPORTED_LANGUAGE_NOTE}` : "분석하지 못했어요. 잠시 뒤 다시 시도해 주세요."}
        </p>
      );
    }
    const progress = state?.progress ?? 0;
    return (
      <div className={styles.progressBox}>
        <p className={styles.progressText}>{STATUS_TEXT[state?.status ?? "queued"]}</p>
        <div className={styles.progressTrack} aria-hidden="true">
          <div className={styles.progressFill} style={{ width: `${Math.round(progress * 100)}%` }} />
        </div>
        <p className={styles.small}>보통 1~2분 걸려요. 끝나면 질문지로 넘어가요.</p>
      </div>
    );
  }
  if (job.kind === "error") {
    return (
      <p className={styles.error}>
        {job.message}{" "}
        {job.needLogin && (
          <a className={styles.textLink} href={LOGIN_URL}>
            다시 로그인
          </a>
        )}
      </p>
    );
  }
  return null;
}

function failure(err: unknown): Job {
  if (err instanceof ApiError) {
    if (err.status === 401) return { kind: "error", message: "로그인이 만료됐어요.", needLogin: true };
    return { kind: "error", message: err.detail, needLogin: false };
  }
  return { kind: "error", message: "서버에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요.", needLogin: false };
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("aborted", "AbortError"));
      },
      { once: true },
    );
  });
}
