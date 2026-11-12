/**
 * 두 제공사를 같은 방법으로 재기 위한 측정기.
 * 제공사 이벤트 대신 실제 소리 크기(마이크, AI 출력)를 봅니다.
 * 그래서 "사용자 말 끝 → AI 목소리 시작"에 위임 왕복까지 포함된, 사용자가 체감하는 지연을 잽니다.
 *
 * 주의: 스피커로 들으면 AI 목소리가 마이크로 들어가 잘못 잽니다. 이어폰을 씁니다.
 */

/** 마이크 RMS 기준. 환경에 따라 조정합니다. */
export const USER_ON = 0.02;
export const AI_ON = 0.01;
/** 이만큼 조용하면 사용자 말 끝 */
export const USER_SILENCE_MS = 500;
/** 이만큼 조용하면 AI 발화 하나가 끝난 것으로 봅니다 (맞장구와 질문을 나누는 기준) */
export const AI_SILENCE_MS = 400;
/**
 * 끼어들기 뒤 AI가 "멈췄다"고 보는 침묵 길이.
 * G1 기준(끊으면 500ms 안에 멈춤)을 숫자로 재야 해서 발화 구분 기준(400ms)보다 짧게 둡니다.
 * 멈춘 시각은 조용해지기 시작한 시각으로 기록하므로, 이 값은 판정 지연만 만들고 측정값에는 더해지지 않습니다.
 */
export const BARGE_STOP_QUIET_MS = 150;
/** 끼어들기 뒤 이 시간 안에 AI가 멈추지 않으면 "안 멈춤"으로 기록합니다 */
export const BARGE_STOP_TIMEOUT_MS = 5000;

export interface MeterEvents {
  userStart(atMs: number): void;
  userEnd(atMs: number): void;
  /** AI 목소리 시작 */
  aiStart(atMs: number): void;
  /** AI 발화 하나가 끝남 (조용해지기 시작한 시각) */
  aiEnd(atMs: number): void;
  /** AI가 말하는 중에 사용자가 말을 시작함 */
  bargeIn(atMs: number): void;
  /** 끼어들기 뒤 AI가 멈춤. stopMs는 끼어들기 시작부터 AI가 조용해진 시각까지, 제한 시간 안에 안 멈추면 null */
  bargeStop(bargeAtMs: number, stopMs: number | null): void;
}

/** performance.now() 값을 epoch 밀리초로 바꿉니다 (계약의 시각은 epoch ms). */
export const epochMs = (perfNow: number) => Math.round(performance.timeOrigin + perfNow);

export class Meter {
  userSpeaking = false;
  aiSpeaking = false;
  /** 마지막 사용자 발화 시작·끝 (epoch ms) */
  lastUserStart: number | null = null;
  lastUserEnd: number | null = null;
  private userQuietSince = 0;
  private aiQuietSince = 0;
  /** 진행 중인 끼어들기 시작 (performance.now) */
  private bargeAt: number | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(private ev: MeterEvents) {}

  start(user: () => AnalyserNode | null, ai: () => AnalyserNode | null) {
    let ub: Float32Array<ArrayBuffer> | null = null;
    let ab: Float32Array<ArrayBuffer> | null = null;
    this.timer = setInterval(() => {
      const now = performance.now();
      const u = user();
      if (u) {
        if (ub?.length !== u.fftSize) ub = new Float32Array(u.fftSize);
        this.feedUser(rms(u, ub), now);
      }
      const a = ai();
      if (a) {
        if (ab?.length !== a.fftSize) ab = new Float32Array(a.fftSize);
        this.feedAi(rms(a, ab), now);
      }
    }, 30);
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  feedUser(level: number, now: number) {
    if (level > USER_ON) {
      if (!this.userSpeaking) {
        this.userSpeaking = true;
        this.lastUserStart = epochMs(now);
        this.ev.userStart(this.lastUserStart);
        if (this.aiSpeaking) {
          this.ev.bargeIn(this.lastUserStart);
          if (this.bargeAt === null) this.bargeAt = now;
        }
      }
      this.userQuietSince = 0;
    } else if (this.userSpeaking) {
      if (!this.userQuietSince) this.userQuietSince = now;
      if (now - this.userQuietSince > USER_SILENCE_MS) {
        this.userSpeaking = false;
        this.lastUserEnd = epochMs(this.userQuietSince);
        this.ev.userEnd(this.lastUserEnd);
      }
    }
  }

  feedAi(level: number, now: number) {
    if (level > AI_ON) {
      if (!this.aiSpeaking) {
        this.aiSpeaking = true;
        this.ev.aiStart(epochMs(now));
      }
      this.aiQuietSince = 0;
    } else if (this.aiSpeaking) {
      if (!this.aiQuietSince) this.aiQuietSince = now;
      if (now - this.aiQuietSince > AI_SILENCE_MS) {
        this.aiSpeaking = false;
        this.ev.aiEnd(epochMs(this.aiQuietSince));
      }
    }
    this.checkBargeStop(level, now);
  }

  private checkBargeStop(level: number, now: number) {
    if (this.bargeAt === null) return;
    if (level <= AI_ON && this.aiQuietSince && now - this.aiQuietSince >= BARGE_STOP_QUIET_MS) {
      const stopMs = Math.max(0, Math.round(this.aiQuietSince - this.bargeAt));
      this.ev.bargeStop(epochMs(this.bargeAt), stopMs);
      this.bargeAt = null;
    } else if (now - this.bargeAt > BARGE_STOP_TIMEOUT_MS) {
      this.ev.bargeStop(epochMs(this.bargeAt), null);
      this.bargeAt = null;
    }
  }
}

function rms(analyser: AnalyserNode, buf: Float32Array<ArrayBuffer>): number {
  analyser.getFloatTimeDomainData(buf);
  let s = 0;
  for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
  return Math.sqrt(s / buf.length);
}
