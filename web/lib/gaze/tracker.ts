/**
 * 웹캠 시선 측정 (#177).
 * 카메라 영상은 이 브라우저 안에서만 처리하고, 프레임도 랜드마크도 서버로 보내거나 저장하지 않습니다.
 * 서버에는 면접을 끝낼 때 요약 숫자(계약 GazeSummary)만 보냅니다.
 * 답변별 화면 밖 비율(#180)도 브라우저에만 들고 있다가 끝낼 때 같이 보냅니다. 도중에 끄면 버려서 서버로 가지 않습니다.
 * 얼굴 랜드마크는 MediaPipe FaceLandmarker로 뽑습니다. 프로그램(wasm)과 모델 파일은 우리 웹에서 받고, 영상은 올리지 않습니다.
 */
import type { GazeAnswer, GazeSummary } from "@gitinsight/contracts";
import type { FaceLandmarker } from "@mediapipe/tasks-vision";
import { type GazeFeatures, type GazeZone, baselineOf, classify, featuresOf } from "./classify";

/**
 * 프로그램(wasm)과 모델은 우리 웹이 같은 출처에서 내려 줍니다 (#181). 시연장에서 외부 CDN이 막혀도 시작할 수 있게 합니다.
 * - wasm: dev·build 전에 설치된 패키지에서 web/public/mediapipe/wasm으로 복사 (web/scripts/copy-mediapipe-wasm.mjs)
 * - 모델: web/public/mediapipe/face_landmarker.task (MediaPipe face_landmarker float16 v1, Apache-2.0, 저장소에 넣음)
 */
const WASM_URL = "/mediapipe/wasm";
const MODEL_URL = "/mediapipe/face_landmarker.task";

/** 초당 측정 횟수. 면접 화면이 버벅이지 않게 낮게 둡니다 */
const SAMPLE_MS = 100;
/** 탭을 오래 비웠다가 돌아온 간격은 이만큼만 셉니다 */
const MAX_STEP_S = 0.5;
/** 보정 시간 */
export const CALIBRATION_MS = 3000;
/** 보정 중 얼굴이 이 비율 넘게 잡혀야 기준으로 씁니다 */
const CALIBRATION_MIN_FACE = 0.6;
/** 화면 밖을 이만큼 넘게 보면 한 번 돌린 것으로 셉니다 */
const AWAY_EVENT_S = 2;
/** 이보다 짧게 쟀으면 요약을 보내지 않습니다 */
const MIN_MEASURED_S = 10;
/**
 * 답변 하나를 이보다 짧게 쟀으면 그 답변은 짚지 않습니다 (#180).
 * 짧은 답변은 1초만 화면 밖을 봐도 비율이 크게 나와서, 전체 요약과 같은 기준을 씁니다.
 */
const MIN_ANSWER_S = MIN_MEASURED_S;

export class GazeError extends Error {}

/** 장면별 시선을 시간으로 쌓아 요약을 만듭니다 */
export class GazeTally {
  private seconds: Record<GazeZone, number> = { camera: 0, screen: 0, away: 0, none: 0 };
  private awayRun = 0;
  private awayCount = 0;

  add(zone: GazeZone, dt: number) {
    this.seconds[zone] += dt;
    if (zone === "away") {
      const before = this.awayRun;
      this.awayRun += dt;
      if (before <= AWAY_EVENT_S && this.awayRun > AWAY_EVENT_S) this.awayCount += 1;
    } else if (zone !== "none") {
      // 얼굴을 놓친 장면은 고개를 크게 돌린 중일 수 있어 이어 셉니다.
      this.awayRun = 0;
    }
  }

  summary(minSeconds = MIN_MEASURED_S): GazeSummary | null {
    const s = this.seconds;
    const total = s.camera + s.screen + s.away + s.none;
    if (total < minSeconds) return null;
    const ratio = (v: number) => Math.round((v / total) * 1000) / 1000;
    return {
      measured_seconds: Math.round(total * 10) / 10,
      camera_ratio: ratio(s.camera),
      screen_ratio: ratio(s.screen),
      away_ratio: ratio(s.away),
      no_face_ratio: ratio(s.none),
      away_count: this.awayCount,
    };
  }
}

async function loadLandmarker(): Promise<FaceLandmarker> {
  const { FaceLandmarker, FilesetResolver } = await import("@mediapipe/tasks-vision");
  const fileset = await FilesetResolver.forVisionTasks(WASM_URL);
  const options = (delegate: "GPU" | "CPU") => ({
    baseOptions: { modelAssetPath: MODEL_URL, delegate },
    runningMode: "VIDEO" as const,
    numFaces: 1,
    outputFaceBlendshapes: true,
  });
  try {
    return await FaceLandmarker.createFromOptions(fileset, options("GPU"));
  } catch {
    // GPU를 못 쓰는 브라우저는 CPU로 돌립니다.
    return await FaceLandmarker.createFromOptions(fileset, options("CPU"));
  }
}

function cameraMessage(e: unknown): string {
  const name = (e as Error)?.name;
  if (name === "NotAllowedError" || name === "SecurityError") return "카메라 권한이 없어요. 주소창의 카메라 권한을 허용해 주세요.";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "카메라를 찾지 못했어요.";
  if (name === "NotReadableError") return "다른 프로그램이 카메라를 쓰고 있어요.";
  return "카메라를 켜지 못했어요.";
}

export class GazeTracker {
  readonly stream: MediaStream;
  private readonly video: HTMLVideoElement;
  private readonly landmarker: FaceLandmarker;
  private baseline: GazeFeatures | null = null;
  private tally = new GazeTally();
  /** 지금 답변 구간의 시선. 질문이 화면에 나올 때마다 새로 셉니다 (#180) */
  private answer = new GazeTally();
  /** 보낸 답변별 화면 밖 비율. turn_id 순서대로, 같은 turn_id는 처음 값만 둡니다 (#180) */
  private readonly answers = new Map<string, number>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private last: number | null = null;
  private closed = false;
  /** 마지막으로 나눈 장면. 실제 웹캠으로 판정 기준을 맞춰 볼 때 화면에 띄웁니다 */
  zone: GazeZone | null = null;

  private constructor(stream: MediaStream, video: HTMLVideoElement, landmarker: FaceLandmarker) {
    this.stream = stream;
    this.video = video;
    this.landmarker = landmarker;
  }

  /** 카메라를 켜고 얼굴 랜드마크 모델을 불러옵니다 */
  static async open(): Promise<GazeTracker> {
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" },
        audio: false,
      });
    } catch (e) {
      throw new GazeError(cameraMessage(e));
    }
    try {
      const video = document.createElement("video");
      video.muted = true;
      video.playsInline = true;
      video.srcObject = stream;
      await video.play();
      const landmarker = await loadLandmarker();
      return new GazeTracker(stream, video, landmarker);
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      throw new GazeError("시선 분석 도구를 불러오지 못했어요. 네트워크를 확인하거나, 체크를 끄고 시선 분석 없이 시작해 주세요.");
    }
  }

  private features(): GazeFeatures | null {
    if (this.video.readyState < 2) return null;
    const result = this.landmarker.detectForVideo(this.video, performance.now());
    const landmarks = result.faceLandmarks[0];
    if (!landmarks) return null;
    return featuresOf(landmarks, result.faceBlendshapes[0]?.categories ?? []);
  }

  private get cameraEnded(): boolean {
    return this.stream.getVideoTracks().every((t) => t.readyState === "ended");
  }

  /** 카메라를 보는 동안 기준을 잡습니다. 얼굴이 잘 안 잡히면 false입니다 */
  async calibrate(ms = CALIBRATION_MS): Promise<boolean> {
    const samples: GazeFeatures[] = [];
    let frames = 0;
    const until = performance.now() + ms;
    while (performance.now() < until && !this.closed) {
      frames += 1;
      const f = this.features();
      if (f) samples.push(f);
      await new Promise((r) => setTimeout(r, SAMPLE_MS));
    }
    if (frames === 0 || samples.length / frames < CALIBRATION_MIN_FACE) return false;
    this.baseline = baselineOf(samples);
    return this.baseline !== null;
  }

  /** 보정한 뒤 측정을 시작합니다. 탭을 보고 있지 않은 시간은 세지 않습니다 */
  start() {
    const baseline = this.baseline;
    if (!baseline || this.timer || this.closed) return;
    this.timer = setInterval(() => {
      if (document.hidden || this.cameraEnded) {
        this.last = null;
        return;
      }
      const now = performance.now();
      const zone = classify(this.features(), baseline);
      this.zone = zone;
      if (this.last !== null) {
        const dt = Math.min((now - this.last) / 1000, MAX_STEP_S);
        this.tally.add(zone, dt);
        this.answer.add(zone, dt);
      }
      this.last = now;
    }, SAMPLE_MS);
  }

  /**
   * 새 답변 구간을 시작합니다 (#180). 질문이 화면에 나올 때 부릅니다.
   * 면접관이 질문을 말하는 시간과 생각하는 시간도 답변 구간에 넣습니다. 그동안의 시선도 면접 태도이기 때문입니다.
   */
  beginAnswer() {
    this.answer = new GazeTally();
  }

  /**
   * 답변을 보낼 때 지금 답변 구간의 화면 밖 비율을 그 turn_id로 적어 둡니다 (#180).
   * 구간은 그대로 두고, 같은 turn_id로 다시 보내면 처음 값을 유지합니다.
   * 카메라를 껐거나 너무 짧게 쟀으면 적지 않습니다. 서버에 가지 않은 turn_id는 서버가 버립니다.
   */
  markAnswer(turnId: string) {
    if (this.closed || this.answers.has(turnId)) return;
    const s = this.answer.summary(MIN_ANSWER_S);
    if (s) this.answers.set(turnId, s.away_ratio);
  }

  /** 끝낼 때 보내는 답변별 화면 밖 비율입니다 (#180) */
  answerRatios(): GazeAnswer[] {
    return [...this.answers].map(([turn_id, away_ratio]) => ({ turn_id, away_ratio }));
  }

  /** 측정을 멈추고 카메라를 끈 뒤 요약을 돌려줍니다. 너무 짧게 쟀으면 null입니다 */
  finish(): GazeSummary | null {
    this.close();
    return this.tally.summary();
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.stream.getTracks().forEach((t) => t.stop());
    this.video.srcObject = null;
    this.landmarker.close();
  }
}
