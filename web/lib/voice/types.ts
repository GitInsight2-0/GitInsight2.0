import type { VoiceToken } from "@gitinsight/contracts";

export type Provider = VoiceToken["provider"];

/** 제공사 어댑터가 공통 진행기에 알려 주는 일 */
export interface AdapterHooks {
  /** 음성 AI가 도구를 불렀습니다. respondTool로 답해야 합니다. */
  onToolCall(callId: string, name: string, args: Record<string, unknown>): void;
  /** 사용자 발화 받아쓰기 (완성된 한 덩어리) */
  onUserTranscript(text: string): void;
  /** 음성 AI 발화 자막 (완성된 한 덩어리) */
  onAiTranscript(text: string, interrupted: boolean): void;
  onError(code: string, message: string): void;
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
  stop(): void;
}

export type Connect = (token: VoiceToken, hooks: AdapterHooks) => Promise<AdapterHandle>;
