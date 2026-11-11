/**
 * 두 음성 제공사에 똑같이 쓰는 지시문과 도구 정의입니다.
 * 비교가 공정하려면 이 값이 같아야 합니다.
 * 토큰 발급이 FastAPI로 옮겨 가면 이 내용도 서버가 VoiceToken.instructions, tools로 내려줍니다.
 */

export const DELEGATION_TOOL = "submit_turn";

/**
 * 음성 AI 지시문.
 * filler가 켜지면 도구를 부르기 전에 짧게 반응해, 위임과 모델 생성 시간 동안의 침묵을 줄입니다 (#28).
 */
export function buildInstructions(opts: { filler: boolean }): string {
  const waitRule = opts.filler
    ? `사용자가 말을 마치면, 도구를 호출하기 전에 "네." 또는 "네, 알겠습니다." 중 하나만 짧게 말한다. 그 밖의 말은 덧붙이지 않고, 바로 도구를 호출한다.`
    : "도구를 기다리는 동안에는 아무 말도 하지 않는다.";
  const firstRule = opts.filler
    ? `사용자가 무슨 말이든 하고 나면, 짧은 맞장구 다음에 반드시 ${DELEGATION_TOOL} 도구를 호출한다. 예외는 없다.`
    : `사용자가 무슨 말이든 하고 나면, 너는 말하기 전에 반드시 먼저 ${DELEGATION_TOOL} 도구를 호출한다. 예외는 없다.`;
  return `
너는 한국어 기술면접관의 목소리다.
무엇을 물을지, 질문을 다시 할지, 힌트를 줄지는 모두 면접 서버가 정한다.
질문, 평가, 정답 설명, 질문 반복을 스스로 하지 않는다.

규칙:
1. "[면접 서버]"로 시작하는 메시지가 오면 따옴표 안 문장을 뜻을 바꾸지 말고 자연스러운 구어체로 말한다.
2. ${firstRule}
3. transcript에는 사용자가 한 말을 요약하거나 고치지 말고 들은 그대로 넣는다.
4. 아래 같은 말도 직접 대답하지 말고 똑같이 도구를 호출한다. 이전 질문을 기억하고 있어도 네가 다시 말하지 않는다.
   - "질문 다시 해 주시겠어요?", "다시 말씀해 주세요", "잘 못 들었어요"
   - "모르겠습니다", "힌트 주세요", "잠깐만요"
5. ${waitRule}
6. 도구 결과의 utterance를 뜻을 바꾸지 말고 말한다.
7. 도구 결과의 done이 true면 utterance를 말한 뒤 더는 말하지 않는다.
`.trim();
}

/** 맞장구를 끈 기본 지시문 */
export const INSTRUCTIONS = buildInstructions({ filler: false });

/**
 * 말 끝 판정 설정. 면접 답변은 생각하느라 멈추는 일이 많아, 제공사 기본값보다 덜 민감하게 둡니다.
 * G1 측정에서 기본값(민감도 HIGH)은 2초 멈춤에서 AI가 말을 끊고 시작했습니다.
 */
export interface VadSettings {
  /** 이만큼 조용하면 답변이 끝난 것으로 봅니다 (ms). 길수록 덜 끼어들지만 응답이 늦어집니다. */
  silenceMs: number;
  /** Gemini 말 끝 판정 민감도. "default"는 제공사 기본값(HIGH) */
  endSensitivity: "low" | "high" | "default";
}

export const DEFAULT_VAD: VadSettings = { silenceMs: 2000, endSensitivity: "low" };

export const VAD_LIMITS = { minSilenceMs: 200, maxSilenceMs: 5000 } as const;

export function parseVad(v: unknown): VadSettings {
  const o = (v ?? {}) as Partial<Record<keyof VadSettings, unknown>>;
  const ms = typeof o.silenceMs === "number" && Number.isFinite(o.silenceMs) ? Math.round(o.silenceMs) : DEFAULT_VAD.silenceMs;
  const sens = o.endSensitivity === "low" || o.endSensitivity === "high" || o.endSensitivity === "default" ? o.endSensitivity : DEFAULT_VAD.endSensitivity;
  return {
    silenceMs: Math.min(VAD_LIMITS.maxSilenceMs, Math.max(VAD_LIMITS.minSilenceMs, ms)),
    endSensitivity: sens,
  };
}

export const TOOL_DESCRIPTION = "사용자의 답변을 면접 서버에 보내고 다음에 할 말을 받는다. 사용자가 말을 마칠 때마다 호출한다.";

export const TOOL_PARAMETERS = {
  type: "object",
  properties: {
    transcript: { type: "string", description: "사용자가 방금 한 말 전체" },
  },
  required: ["transcript"],
  additionalProperties: false,
} as const;

/** 음성 AI에 서버 발화를 말하게 할 때 보내는 메시지 */
export const sayMessage = (utterance: string) => `[면접 서버] 다음 문장을 말하세요: "${utterance}"`;

/** 비교 테스트 대본. docs/evals/voice-g1.md와 같습니다. */
export const TEST_SCRIPT = [
  "먼저 깃허브에서 레포를 클론하고, 트리시터로 함수 목록을 뽑은 다음에 FastAPI 라우터로 결과를 넘깁니다.",
  "파일을 세 개만 읽은 건 토큰 한도 때문이었는데, 레포가 크면 중요한 파일을 놓칠 수 있습니다.",
  "(AI가 말하는 도중에 끊고) 잠깐만요, 질문 다시 해 주시겠어요?",
  "useEffect 의존성 배열에 messages를 넣었더니 무한 렌더링이 생겨서 useReducer로 바꿨습니다.",
  "음, 그 부분은 잘 모르겠습니다.",
] as const;
