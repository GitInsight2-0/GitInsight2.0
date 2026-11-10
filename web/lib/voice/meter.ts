/**
 * 두 제공사를 같은 방법으로 재기 위한 측정기.
 * 제공사 이벤트 대신 실제 소리 크기(마이크, AI 출력)를 봅니다.
 * 그래서 "사용자 말 끝 → AI 목소리 시작"에 위임 왕복까지 포함된, 사용자가 체감하는 지연을 잽니다.
 *
 * 주의: 스피커로 들으면 AI 목소리가 마이크로 들어가 잘못 잽니다. 이어폰을 씁니다.
 */

const USER_ON = 0.02; // 마이크 RMS 기준. 환경에 따라 조정합니다.
const AI_ON = 0.01;
const USER_SILENCE_MS = 500; // 이만큼 조용하면 사용자 말 끝
const AI_SILENCE_MS = 400;

export interface MeterEvents {
  userStart(atMs: number): void;
  userEnd(atMs: number): void;
  /** AI 목소리 시작. 직전 사용자 말 끝으로부터의 지연을 함께 줍니다 (없으면 null). */
  aiStart(atMs: number, latencyMs: number | null): void;
  bargeIn(atMs: number): void;
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
  private aiLastEnd = 0;
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
        if (this.aiSpeaking) this.ev.bargeIn(this.lastUserStart);
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
        const at = epochMs(now);
        // 마지막 AI 발화 이후 사용자가 말을 마쳤다면 그 간격이 응답 지연입니다.
        const answered = this.lastUserEnd !== null && this.lastUserEnd > this.aiLastEnd && !this.userSpeaking;
        this.ev.aiStart(at, answered ? at - this.lastUserEnd! : null);
      }
      this.aiQuietSince = 0;
    } else if (this.aiSpeaking) {
      if (!this.aiQuietSince) this.aiQuietSince = now;
      if (now - this.aiQuietSince > AI_SILENCE_MS) {
        this.aiSpeaking = false;
        this.aiLastEnd = epochMs(this.aiQuietSince);
      }
    }
  }
}

function rms(analyser: AnalyserNode, buf: Float32Array<ArrayBuffer>): number {
  analyser.getFloatTimeDomainData(buf);
  let s = 0;
  for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
  return Math.sqrt(s / buf.length);
}
