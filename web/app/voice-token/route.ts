/**
 * Gemini Live 임시 토큰 발급.
 * 실험 모드(LAB_ENABLED=true)에서는 누구나, 그 밖에는 GitHub로 로그인한 사용자만 받습니다.
 *
 * 계약(VoiceToken)상 최종 위치는 FastAPI의 POST /api/sessions/{id}/voice-token입니다 (#34).
 * 그 엔드포인트가 생기기 전까지 같은 응답 형식으로 여기서 발급합니다.
 * /api/* 아래에 두지 않는 것은 그 경로가 통째로 FastAPI로 넘어가기 때문입니다 (next.config.ts).
 * 실제 API 키는 서버에만 두고, 브라우저에는 짧게 사는 임시 토큰만 보냅니다.
 */
import type { VoiceToken } from "@gitinsight/contracts";
import { EndSensitivity, GoogleGenAI, Modality, Type } from "@google/genai";
import { DELEGATION_TOOL, TOOL_DESCRIPTION, TRANSCRIPT_DESCRIPTION, type VadSettings, buildInstructions, parseVad } from "@/lib/voice/config";

export async function POST(req: Request) {
  if (process.env.LAB_ENABLED !== "true") {
    const login = await checkLogin(req);
    if (login !== "ok") return login;
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

/** FastAPI 서버(API_ORIGIN)는 웹 서버에서 이 주소로 닿습니다. next.config.ts의 rewrite와 같은 값입니다 */
const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";

/**
 * 요청에 붙은 로그인 쿠키를 그대로 서버의 GET /api/me에 넘겨 로그인했는지 확인합니다.
 * 쿠키를 여기서 열지 않습니다 (암호화 키는 FastAPI에만 있습니다).
 */
async function checkLogin(req: Request): Promise<"ok" | Response> {
  const cookie = req.headers.get("cookie");
  if (!cookie) return Response.json({ detail: "로그인이 필요합니다" }, { status: 401 });
  try {
    const res = await fetch(`${API_ORIGIN}/api/me`, {
      headers: { cookie },
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (res.ok) return "ok";
    if (res.status === 401) return Response.json({ detail: "로그인이 필요합니다" }, { status: 401 });
    return Response.json({ detail: "로그인을 확인하지 못했습니다" }, { status: 502 });
  } catch {
    return Response.json({ detail: "API 서버에 연결할 수 없습니다" }, { status: 502 });
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
        properties: { transcript: { type: Type.STRING, description: TRANSCRIPT_DESCRIPTION } },
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
