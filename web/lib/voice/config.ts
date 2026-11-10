/**
 * 두 음성 제공사에 똑같이 쓰는 지시문과 도구 정의입니다.
 * 비교가 공정하려면 이 값이 같아야 합니다.
 * 토큰 발급이 FastAPI로 옮겨 가면 이 내용도 서버가 VoiceToken.instructions, tools로 내려줍니다.
 */

export const DELEGATION_TOOL = "submit_turn";

export const INSTRUCTIONS = `
너는 한국어 기술면접관의 목소리다.
무엇을 물을지는 면접 서버가 정한다.
질문, 평가, 정답 설명을 스스로 만들지 않는다.

규칙:
1. "[면접 서버]"로 시작하는 메시지가 오면 따옴표 안 문장을 뜻을 바꾸지 말고 자연스러운 구어체로 말한다.
2. 사용자가 말을 마칠 때마다 ${DELEGATION_TOOL} 도구를 호출하고, transcript에 사용자가 한 말 전체를 넣는다.
3. 사용자가 다시 말해 달라고 하거나 모르겠다고 해도 직접 대응하지 말고 똑같이 도구를 호출한다.
4. 도구를 기다리는 동안에는 "네" 같은 아주 짧은 맞장구만 할 수 있다.
5. 도구 결과의 utterance를 뜻을 바꾸지 말고 말한다.
6. 도구 결과의 done이 true면 utterance를 말한 뒤 더는 말하지 않는다.
`.trim();

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
