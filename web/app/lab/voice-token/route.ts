/**
 * 음성 PoC용 Gemini Live 임시 토큰 발급 (LAB_ENABLED=true일 때만).
 *
 * 계약(VoiceToken)상 최종 위치는 FastAPI의 POST /api/sessions/{id}/voice-token입니다.
 * 그 엔드포인트가 생기기 전까지 같은 응답 형식으로 여기서 발급합니다.
 * 실제 API 키는 서버에만 두고, 브라우저에는 짧게 사는 임시 토큰만 보냅니다.
 */
import type { VoiceToken } from "@gitinsight/contracts";
import { EndSensitivity, GoogleGenAI, Modality, Type } from "@google/genai";
import { DELEGATION_TOOL, TOOL_DESCRIPTION, TOOL_PARAMETERS, type VadSettings, buildInstructions, parseVad } from "@/lib/voice/config";

export async function POST(req: Request) {
  if (process.env.LAB_ENABLED !== "true") {
    return Response.json({ detail: "LAB_ENABLED=true일 때만 쓸 수 있습니다" }, { status: 404 });
  }
  const body = (await req.json().catch(() => null)) as { vad?: unknown; filler?: unknown } | null;
  try {
    const vad = parseVad(body?.vad);
    const instructions = buildInstructions({ filler: body?.filler === true });
    const token = await geminiToken(vad, instructions);
    return Response.json(token satisfies VoiceToken);
  } catch (e) {
    // 제공사 오류 원문은 브라우저로 보내지 않고 서버 터미널에만 남깁니다.
    console.error("[voice-token] Gemini 토큰 발급 실패", e);
    const detail = e instanceof MissingEnvError ? e.message : "음성 연결 토큰을 받지 못했습니다. 잠시 뒤 다시 시도하거나 텍스트로 진행하세요.";
    return Response.json({ detail }, { status: 502 });
  }
}

async function geminiToken(vad: VadSettings, instructions: string): Promise<VoiceToken> {
  const apiKey = requireEnv("GEMINI_API_KEY");
  const model = process.env.GEMINI_LIVE_MODEL || "gemini-3.1-flash-live-preview";
  const ai = new GoogleGenAI({ apiKey, httpOptions: { apiVersion: "v1alpha" } });
  const now = Date.now();
  // 기본 유효 시간: 새 세션 시작 1분, 메시지 전송 30분.
  // https://ai.google.dev/gemini-api/docs/live-api/ephemeral-tokens
  const expireTime = new Date(now + 30 * 60_000).toISOString();
  const functionDeclarations = [
    {
      name: DELEGATION_TOOL,
      description: TOOL_DESCRIPTION,
      parameters: {
        type: Type.OBJECT,
        properties: { transcript: { type: Type.STRING, description: TOOL_PARAMETERS.properties.transcript.description } },
        required: ["transcript"],
      },
    },
  ];
  // 지시문, 도구, 음성 설정을 토큰에 잠가 브라우저가 바꾸지 못하게 합니다.
  const token = await ai.authTokens.create({
    config: {
      uses: 1,
      expireTime,
      newSessionExpireTime: new Date(now + 60_000).toISOString(),
      liveConnectConstraints: {
        model,
        config: {
          responseModalities: [Modality.AUDIO],
          systemInstruction: { parts: [{ text: instructions }] },
          speechConfig: {
            languageCode: "ko-KR",
            voiceConfig: { prebuiltVoiceConfig: { voiceName: process.env.GEMINI_VOICE || "Kore" } },
          },
          inputAudioTranscription: {},
          outputAudioTranscription: {},
          sessionResumption: {},
          // 말 끝 판정. https://ai.google.dev/api/live 의 AutomaticActivityDetection
          realtimeInputConfig: {
            automaticActivityDetection: {
              silenceDurationMs: vad.silenceMs,
              ...(vad.endSensitivity === "default"
                ? {}
                : {
                    endOfSpeechSensitivity:
                      vad.endSensitivity === "low" ? EndSensitivity.END_SENSITIVITY_LOW : EndSensitivity.END_SENSITIVITY_HIGH,
                  }),
            },
          },
          tools: [{ functionDeclarations }],
        },
      },
      httpOptions: { apiVersion: "v1alpha" },
    },
  });
  if (!token.name) throw new Error("token.name이 비어 있습니다");
  return {
    provider: "gemini",
    client_secret: token.name,
    expires_at: expireTime,
    model,
    instructions,
    tools: [{ functionDeclarations }],
  };
}

/** 키가 없는 설정 오류. 개발자가 바로 고칠 수 있게 이 문구는 그대로 보여 줍니다 (값은 들어가지 않음) */
class MissingEnvError extends Error {}

function requireEnv(name: string): string {
  const v = process.env[name];
  if (!v) throw new MissingEnvError(`${name}가 없습니다 (web/.env.local)`);
  return v;
}
