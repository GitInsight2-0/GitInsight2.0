"use client";

import type { AnalysisState, Repo } from "@gitinsight/contracts";
import { useEffect, useRef, useState } from "react";
import { type ReposResult, STATUS_TEXT, fetchAnalysis, fetchRepos, interviewHref, startAnalysis } from "@/lib/analyses";
import { DEMO_ANALYSIS_ID, DEMO_REPO } from "@/lib/demo";
import { explainError } from "@/lib/voice/errors";
import styles from "./home.module.css";

/** 분석 진행을 다시 묻는 간격 */
const POLL_MS = 2000;
/** 레포가 이보다 많으면 검색 칸을 보여 줍니다 */
const SEARCH_FROM = 6;

type Job =
  | { kind: "idle" }
  | { kind: "starting"; repo: Repo }
  | { kind: "running"; repo: Repo; analysisId: string; state: AnalysisState | null }
  | { kind: "error"; repo: Repo; message: string };

/**
 * 로그인 뒤 레포를 고르고 분석을 기다리는 곳 (#61).
 * 서버에 레포 목록 API가 아직 없거나 목록이 비면 데모 레포로 시작할 수 있게 둡니다.
 */
export default function RepoPicker() {
  const [repos, setRepos] = useState<ReposResult | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [job, setJob] = useState<Job>({ kind: "idle" });
  const jobAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    fetchRepos(ac.signal)
      .then(setRepos)
      .catch((e: unknown) => {
        if (!ac.signal.aborted) setRepos({ kind: "error", status: 0, message: explainError(e) });
      });
    return () => {
      ac.abort();
      jobAbort.current?.abort();
    };
  }, []);

  async function analyze(repo: Repo) {
    jobAbort.current?.abort();
    const ac = new AbortController();
    jobAbort.current = ac;
    setJob({ kind: "starting", repo });
    try {
      const created = await startAnalysis(repo.repo_id, ac.signal);
      let state: AnalysisState | null =
        created.status === "done" ? { status: "done", progress: 1, commit_sha: null, error: null } : null;
      setJob({ kind: "running", repo, analysisId: created.analysis_id, state });
      // 끝나거나 실패할 때까지 진행을 다시 묻습니다.
      while (state?.status !== "done" && state?.status !== "failed") {
        await sleep(POLL_MS, ac.signal);
        state = await fetchAnalysis(created.analysis_id, ac.signal);
        setJob({ kind: "running", repo, analysisId: created.analysis_id, state });
      }
    } catch (e) {
      if (ac.signal.aborted) return;
      setJob({ kind: "error", repo, message: explainError(e) });
    }
  }

  function cancel() {
    jobAbort.current?.abort();
    setJob({ kind: "idle" });
  }

  if (repos === null) return <p className={styles.muted}>레포 목록을 불러오고 있어요…</p>;

  // 레포 목록 API가 아직 없음: 데모 레포로 시작 (2단계 전 동작)
  if (repos.kind === "missing") return <DemoStart note="레포 선택이 열리기 전까지는 준비된 데모 레포로 면접을 봅니다." />;

  if (repos.kind === "error") {
    return (
      <>
        <p className={styles.error}>레포 목록을 불러오지 못했어요. ({repos.message})</p>
        <DemoStart note="데모 레포로는 바로 면접을 볼 수 있어요." secondary />
      </>
    );
  }

  if (repos.repos.length === 0) {
    return (
      <>
        <p className={styles.muted}>
          아직 읽을 수 있는 레포가 없어요. GitHub에서 우리 서비스 앱을 면접 볼 레포에 설치하면 여기에 나타나요.
        </p>
        <DemoStart note="데모 레포로는 바로 면접을 볼 수 있어요." secondary />
      </>
    );
  }

  if (job.kind !== "idle") return <JobView job={job} onRetry={() => void analyze(job.repo)} onBack={cancel} />;

  const q = query.trim().toLowerCase();
  const shown = q ? repos.repos.filter((r) => r.full_name.toLowerCase().includes(q)) : repos.repos;
  const pick = repos.repos.find((r) => r.repo_id === selected) ?? null;

  return (
    <div className={styles.picker}>
      <fieldset className={styles.repoList}>
        <legend className={styles.label}>면접 볼 레포를 고르세요</legend>
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
      <a className={styles.textLink} href={interviewHref(DEMO_ANALYSIS_ID, DEMO_REPO)}>
        데모 레포({DEMO_REPO})로 바로 시작
      </a>
    </div>
  );
}

function JobView({ job, onRetry, onBack }: { job: Exclude<Job, { kind: "idle" }>; onRetry: () => void; onBack: () => void }) {
  const status = job.kind === "running" ? job.state?.status ?? "queued" : null;
  const progress = job.kind === "running" ? job.state?.progress ?? 0 : 0;
  const failed = job.kind === "error" || status === "failed";
  const message =
    job.kind === "error" ? job.message : job.kind === "running" && status === "failed" ? job.state?.error ?? "" : "";
  return (
    <div className={styles.picker}>
      <div className={styles.repo}>
        <span className={styles.label}>면접 볼 레포</span>
        <span className={styles.repoName}>{job.repo.full_name}</span>
      </div>

      {!failed && (
        <div className={styles.progressBox} role="status" aria-live="polite">
          <span className={styles.progressText}>
            {job.kind === "starting" ? "분석을 요청하고 있어요" : STATUS_TEXT[status ?? "queued"]}
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
          {status !== "done" && <span className={styles.muted}>레포 크기에 따라 몇 분 걸릴 수 있어요.</span>}
        </div>
      )}

      {failed && (
        <p role="alert" className={styles.error}>
          분석하지 못했어요.{message ? ` (${message})` : ""}
        </p>
      )}

      {job.kind === "running" && status === "done" && (
        <a className={styles.primary} href={interviewHref(job.analysisId, job.repo.full_name)}>
          면접 시작
        </a>
      )}
      {failed && (
        <button type="button" className={styles.primary} onClick={onRetry}>
          다시 분석
        </button>
      )}
      {status !== "done" && (
        <button type="button" className={styles.ghost} onClick={onBack}>
          {failed ? "다른 레포 고르기" : "그만 기다리고 다른 레포 고르기"}
        </button>
      )}
    </div>
  );
}

function DemoStart({ note, secondary = false }: { note: string; secondary?: boolean }) {
  return (
    <>
      <div className={styles.repo}>
        <span className={styles.label}>면접 볼 레포</span>
        <span className={styles.repoName}>{DEMO_REPO}</span>
        <span className={styles.muted}>{note}</span>
      </div>
      <a className={secondary ? styles.secondary : styles.primary} href={interviewHref(DEMO_ANALYSIS_ID, DEMO_REPO)}>
        {secondary ? "데모 레포로 면접 시작" : "면접 시작"}
      </a>
    </>
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
