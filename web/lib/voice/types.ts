import type { VoiceToken } from "@gitinsight/contracts";

/** 음성 AI 제공사. G1에서 Gemini Live로 정했습니다 (docs/evals/voice-g1.md). */
export const PROVIDER: VoiceToken["provider"] = "gemini";

/**
 * 받아쓰기와 도구 호출이 어느 사용자 턴에 속하는지 나타내는 키.
 * Gemini 어댑터가 사용자 턴마다 매기는 번호입니다.
 * 진행기는 이 키로 늦게 도착한 받아쓰기를 이전 턴 것으로 판단합니다 (시간 창을 쓰지 않음).
 */
export type TurnKey = string;

/** 제공사 어댑터가 공통 진행기에 알려 주는 일 */
export interface AdapterHooks {
  /** 음성 AI가 도구를 불렀습니다. respondTool로 답해야 합니다. turnKey는 이 호출이 답하는 사용자 턴입니다. */
  onToolCall(callId: string, name: string, args: Record<string, unknown>, turnKey: TurnKey | null): void;
  /** 사용자 발화 받아쓰기 (완성된 한 덩어리). turnKey는 이 말이 속한 사용자 턴입니다. */
  onUserTranscript(text: string, turnKey: TurnKey | null): void;
  /** 음성 AI 발화 자막 (완성된 한 덩어리) */
  onAiTranscript(text: string, interrupted: boolean): void;
  onError(code: string, message: string): void;
  /** 제공사 쪽에서 연결이 닫혔습니다. 진행기는 면접을 끝냅니다. */
  onClosed(reason: string): void;
}

/** 제공사별 연결. 소리 입출력과 프로토콜만 맡고, 면접 진행은 모릅니다. */
export interface AdapterHandle {
  /** 서버가 정한 문장을 말하게 합니다. */
  say(text: string): void;
  respondTool(callId: string, name: string, output: Record<string, unknown>): void;
  /** 측정용 소리 크기 */
  userAnalyser(): AnalyserNode | null;
  aiAnalyser(): AnalyserNode | null;
  /** 마이크, 오디오, 연결을 모두 닫습니다. 여러 번 불러도 됩니다. */
  stop(): void;
}

export interface ConnectOptions {
  /** 연결 중에 멈추면 어댑터가 자원을 정리하고 AbortError로 끝냅니다. */
  signal: AbortSignal;
}

export type Connect = (token: VoiceToken, hooks: AdapterHooks, opts: ConnectOptions) => Promise<AdapterHandle>;
