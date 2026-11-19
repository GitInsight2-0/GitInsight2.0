"use client";

import type { AnalysisState, Repo } from "@gitinsight/contracts";
import { useEffect, useRef, useState } from "react";
import {
  type ReposResult,
  STATUS_TEXT,
  SUPPORTED_LANGUAGE_NOTE,
  fetchAnalysis,
  fetchRepos,
  interviewHref,
  isRetryablePollError,
  isUnsupportedCode,
  startAnalysis,
} from "@/lib/analyses";
import { DEMO_ANALYSIS_ID, DEMO_REPO } from "@/lib/demo";
import { explainError } from "@/lib/voice/errors";
import styles from "./home.module.css";

/** 분석 진행을 다시 묻는 간격 */
const POLL_MS = 2000;
/** 진행 상태 조회가 잇달아 실패해도 다시 묻는 횟수. 2초 간격이라 10초쯤 더 기다립니다 (#102) */
const POLL_RETRIES = 5;
/** 레포가 이보다 많으면 검색 칸을 보여 줍니다 */
const SEARCH_FROM = 6;

type Job =
  | { kind: "idle" }
  | { kind: "starting"; repo: Repo }
  /** checking: 진행 상태 조회가 실패해서 다시 묻는 중 */
  | { kind: "running"; repo: Repo; analysisId: string; state: AnalysisState | null; checking: boolean }
  /** 분석 요청 자체가 실패함 */
  | { kind: "error"; repo: Repo; message: string }
  /**
   * 분석은 요청했지만 진행 상태를 끝내 확인하지 못함. 서버에서는 분석이 계속되고 있을 수 있습니다.
   * canResume: 일시 오류라서 다시 물어볼 만함 (401, 404는 다시 물어도 같아서 false)
   */
  | { kind: "lost"; repo: Repo; analysisId: string; message: string; canResume: boolean };

/** 첫 화면의 단계 (개편 #125). 순서가 실제 진행 순서입니다 */
const STEPS = ["GitHub 연결", "레포 고르기", "분석", "면접"] as const;

/** 지금 단계(0부터)를 표시합니다. 앞 단계는 끝남, 지금 단계는 진행 중입니다 */
export function Steps({ at }: { at: number }) {
  return (
    <ol className={styles.steps} aria-label="진행 단계">
      {STEPS.map((name, i) => (
        <li
          key={name}
          className={i < at ? styles.stepDone : i === at ? styles.stepOn : undefined}
          aria-current={i === at ? "step" : undefined}
        >
          <span className={styles.stepNo}>{i < at ? "✓" : i + 1}</span>
          {name}
        </li>
      ))}
    </ol>
  );
}

/**
 * 로그인 뒤 레포를 고르고 분석을 기다리는 곳 (#61).
 * 레포 목록 API가 없는 서버(로컬·실험 모드)에서만 데모 레포로 시작할 수 있게 둡니다.
 * 배포처럼 목록을 받는 서버에서는 데모 분석이 DB에 없어서 데모 시작을 감춥니다 (#104).
 */
export default function RepoPicker() {
  const [repos, setRepos] = useState<ReposResult | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [job, setJob] = useState<Job>({ kind: "idle" });
  const jobAbort = useRef<AbortController | null>(null);
  const reposAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    loadRepos();
    return () => {
      reposAbort.current?.abort();
      jobAbort.current?.abort();
    };
  }, []);

  function loadRepos() {
    reposAbort.current?.abort();
    const ac = new AbortController();
    reposAbort.current = ac;
    setRepos(null);
    fetchRepos(ac.signal)
      .then(setRepos)
      .catch((e: unknown) => {
        if (!ac.signal.aborted) setRepos({ kind: "error", status: 0, message: explainError(e) });
      });
  }

  function begin(): AbortController {
    jobAbort.current?.abort();
    const ac = new AbortController();
    jobAbort.current = ac;
    return ac;
  }

  async function analyze(repo: Repo) {
    const ac = begin();
    setJob({ kind: "starting", repo });
    let created;
    try {
      created = await startAnalysis(repo.repo_id, ac.signal);
    } catch (e) {
      if (ac.signal.aborted) return;
      setJob({ kind: "error", repo, message: explainError(e) });
      return;
    }
    const state: AnalysisState | null =
      created.status === "done" ? { status: "done", progress: 1, commit_sha: null, error: null } : null;
    await poll(repo, created.analysis_id, state, false, ac);
  }

  /** 진행 상태를 끝내 확인하지 못한 분석을 다시 묻습니다. 분석 요청은 다시 보내지 않습니다 */
  function resume(repo: Repo, analysisId: string) {
    void poll(repo, analysisId, null, true, begin());
  }

  /**
   * 끝나거나 실패할 때까지 진행을 다시 묻습니다.
   * 조회가 일시 오류로 실패하면 진행 화면을 그대로 두고 몇 번 더 묻습니다 (#102).
   */
  async function poll(repo: Repo, analysisId: string, first: AnalysisState | null, checking: boolean, ac: AbortController) {
    let state = first;
    let failures = 0;
    setJob({ kind: "running", repo, analysisId, state, checking });
    while (state?.status !== "done" && state?.status !== "failed") {
      try {
        await sleep(POLL_MS, ac.signal);
        state = await fetchAnalysis(analysisId, ac.signal);
        failures = 0;
        setJob({ kind: "running", repo, analysisId, state, checking: false });
      } catch (e) {
        if (ac.signal.aborted) return;
        failures += 1;
        const retryable = isRetryablePollError(e);
        if (!retryable || failures > POLL_RETRIES) {
          setJob({ kind: "lost", repo, analysisId, message: explainError(e), canResume: retryable });
          return;
        }
        setJob({ kind: "running", repo, analysisId, state, checking: true });
      }
    }
  }

  function cancel() {
    jobAbort.current?.abort();
    setJob({ kind: "idle" });
  }

  if (repos === null) {
    return (
      <>
        <Steps at={1} />
        <p className={styles.muted}>레포 목록을 불러오고 있어요…</p>
      </>
    );
  }

  // 레포 목록 API가 없는 서버(로컬·실험 모드): 데모 레포로 시작
  if (repos.kind === "missing") {
    return (
      <>
        <Steps at={1} />
        <DemoStart note="레포 선택이 열리지 않은 서버라 준비된 데모 레포로 면접을 봅니다." />
      </>
    );
  }

  if (repos.kind === "error") {
    return (
      <>
        <Steps at={1} />
        <p role="alert" className={styles.error}>
          레포 목록을 불러오지 못했어요. 잠시 뒤 다시 불러와 주세요. ({repos.message})
        </p>
        <button type="button" className={styles.secondary} onClick={loadRepos}>
          다시 불러오기
        </button>
      </>
    );
  }

  if (repos.repos.length === 0) {
    return (
      <>
        <Steps at={1} />
        <h2 className={styles.cardTitle}>면접 볼 레포가 아직 없어요</h2>
        <p className={styles.muted}>
          GitHub에서 GitInsight 앱을 면접 볼 레포에 설치하면 여기에 나타나요. 설치한 뒤 다시 불러와 주세요.
        </p>
        <button type="button" className={styles.secondary} onClick={loadRepos}>
          다시 불러오기
        </button>
      </>
    );
  }

  if (job.kind !== "idle") {
    const done = job.kind === "running" && job.state?.status === "done";
    return (
      <>
        <Steps at={done ? 3 : 2} />
        <JobView
          job={job}
          onRetry={() => void analyze(job.repo)}
          onResume={() => job.kind === "lost" && resume(job.repo, job.analysisId)}
          onBack={cancel}
        />
      </>
    );
  }

  const q = query.trim().toLowerCase();
  const shown = q ? repos.repos.filter((r) => r.full_name.toLowerCase().includes(q)) : repos.repos;
  const pick = repos.repos.find((r) => r.repo_id === selected) ?? null;

  return (
    <>
      <Steps at={1} />
      <div className={styles.picker}>
        <fieldset className={styles.repoList}>
          <legend className={styles.cardTitle}>면접 볼 레포를 고르세요</legend>
          <p className={styles.muted}>{SUPPORTED_LANGUAGE_NOTE}</p>
          {repos.repos.length >= SEARCH_FROM && (
            <input
              type="search"
              className={styles.search}
              placeholder="레포 이름으로 찾기"
              aria-label="레포 이름으로 찾기"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          )}
          <div className={styles.repoScroll}>
            {shown.map((r) => (
              <label key={r.repo_id} className={r.repo_id === selected ? `${styles.repoRow} ${styles.repoRowOn}` : styles.repoRow}>
                <input
                  type="radio"
                  name="repo"
                  value={r.repo_id}
                  checked={r.repo_id === selected}
                  onChange={() => setSelected(r.repo_id)}
                />
                <span className={styles.repoName}>{r.full_name}</span>
                {r.private && <span className={styles.badge}>비공개</span>}
              </label>
            ))}
            {shown.length === 0 && <p className={styles.muted}>"{query}"에 맞는 레포가 없어요.</p>}
          </div>
        </fieldset>
        <button type="button" className={styles.primary} disabled={!pick} onClick={() => pick && void analyze(pick)}>
          분석하고 면접 시작
        </button>
      </div>
    </>
  );
}

function JobView({
  job,
  onRetry,
  onResume,
  onBack,
}: {
  job: Exclude<Job, { kind: "idle" }>;
  onRetry: () => void;
  onResume: () => void;
  onBack: () => void;
}) {
  const status = job.kind === "running" ? job.state?.status ?? "queued" : null;
  const progress = job.kind === "running" ? job.state?.progress ?? 0 : 0;
  const checking = job.kind === "running" && job.checking;
  const lost = job.kind === "lost";
  const failed = job.kind === "error" || lost || status === "failed";
  const message =
    job.kind === "error" || job.kind === "lost"
      ? job.message
      : job.kind === "running" && status === "failed"
        ? job.state?.error ?? ""
        : "";
  // 지원하는 언어의 코드가 없는 레포는 다시 분석해도 같은 결과라, 다른 레포를 고르게 먼저 안내합니다 (#71).
  const unsupported = job.kind === "running" && status === "failed" && isUnsupportedCode(job.state?.error);
  return (
    <div className={styles.picker}>
      <div className={styles.repo}>
        <span className={styles.label}>면접 볼 레포</span>
        <span className={styles.repoName}>{job.repo.full_name}</span>
      </div>

      {!failed && (
        <div className={styles.progressBox} role="status" aria-live="polite">
          <span className={styles.progressText}>
            {job.kind === "starting"
              ? "분석을 요청하고 있어요"
              : checking && !job.state
                ? "진행 상태를 확인하고 있어요"
                : STATUS_TEXT[status ?? "queued"]}
          </span>
          <div
            className={styles.progressTrack}
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(progress * 100)}
            aria-label="분석 진행률"
          >
            <div className={styles.progressFill} style={{ width: `${Math.max(4, Math.round(progress * 100))}%` }} />
          </div>
          {status !== "done" && (
            <span className={styles.muted}>
              {checking
                ? "연결이 잠시 불안정해서 진행 상태를 다시 확인하고 있어요."
                : "레포 크기에 따라 몇 분 걸릴 수 있어요."}
            </span>
          )}
        </div>
      )}

      {failed &&
        (unsupported ? (
          <p role="alert" className={styles.error}>
            이 레포에서 Python 코드를 찾지 못했어요. 다른 레포를 골라 주세요.
          </p>
        ) : lost ? (
          <p role="alert" className={styles.error}>
            분석 진행 상태를 확인하지 못했어요.
            {job.canResume ? " 분석은 서버에서 계속되고 있을 수 있어요." : ""}
            {message ? ` (${message})` : ""}
          </p>
        ) : job.kind === "error" ? (
          <p role="alert" className={styles.error}>
            분석을 요청하지 못했어요.{message ? ` (${message})` : ""}
          </p>
        ) : (
          <p role="alert" className={styles.error}>
            분석하지 못했어요.{message ? ` (${message})` : ""}
          </p>
        ))}

      <div className={styles.actions}>
        {job.kind === "running" && status === "done" && (
          <a className={styles.primary} href={interviewHref(job.analysisId, job.repo.full_name)}>
            면접 시작
          </a>
        )}
        {unsupported && (
          <>
            <button type="button" className={styles.primary} onClick={onBack}>
              다른 레포 고르기
            </button>
            <button type="button" className={styles.secondary} onClick={onRetry}>
              다시 분석
            </button>
          </>
        )}
        {lost && job.canResume && (
          <button type="button" className={styles.primary} onClick={onResume}>
            진행 상태 다시 확인
          </button>
        )}
        {failed && !unsupported && !lost && (
          <button type="button" className={styles.primary} onClick={onRetry}>
            다시 분석
          </button>
        )}
        {status !== "done" && !unsupported && (
          <button type="button" className={styles.secondary} onClick={onBack}>
            {failed ? "다른 레포 고르기" : "그만 기다리고 다른 레포 고르기"}
          </button>
        )}
      </div>
    </div>
  );
}

/** 레포 목록 API가 없는 서버에서만 씁니다 (#104) */
function DemoStart({ note }: { note: string }) {
  return (
    <div className={styles.picker}>
      <div className={styles.repo}>
        <span className={styles.label}>면접 볼 레포</span>
        <span className={styles.repoName}>{DEMO_REPO}</span>
        <span className={styles.muted}>{note}</span>
      </div>
      <a className={styles.primary} href={interviewHref(DEMO_ANALYSIS_ID, DEMO_REPO)}>
        면접 시작
      </a>
    </div>
  );
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        reject(signal.reason);
      },
      { once: true },
    );
  });
}
