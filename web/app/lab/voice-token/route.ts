/**
 * 음성 PoC용 임시 토큰 발급 (LAB_ENABLED=true일 때만).
 *
 * 계약(VoiceToken)상 최종 위치는 FastAPI의 POST /api/sessions/{id}/voice-token입니다.
 * 그 엔드포인트가 생기기 전까지 같은 응답 형식으로 여기서 발급합니다.
 * 실제 API 키는 서버에만 두고, 브라우저에는 짧게 사는 임시 토큰만 보냅니다.
 */
import type { VoiceToken } from "@gitinsight/contracts";
import { EndSensitivity, GoogleGenAI, Modality, Type } from "@google/genai";
import { DELEGATION_TOOL, INSTRUCTIONS, TOOL_DESCRIPTION, TOOL_PARAMETERS, type VadSettings, parseVad } from "@/lib/voice/config";

type Provider = VoiceToken["provider"];

export async function POST(req: Request) {
  if (process.env.LAB_ENABLED !== "true") {
    return Response.json({ detail: "LAB_ENABLED=true일 때만 쓸 수 있습니다" }, { status: 404 });
  }
  const body = (await req.json().catch(() => null)) as { provider?: unknown; vad?: unknown } | null;
  const provider = body?.provider;
  if (provider !== "openai" && provider !== "gemini") {
    return Response.json({ detail: "provider는 openai 또는 gemini입니다" }, { status: 400 });
  }
  try {
    const vad = parseVad(body?.vad);
    const token = provider === "openai" ? await openaiToken(vad) : await geminiToken(vad);
    return Response.json(token satisfies VoiceToken);
  } catch (e) {
    // 제공사 오류 본문에 키가 들어가지는 않지만, 길이를 줄여 돌려줍니다.
    return Response.json({ detail: `${provider} 토큰 발급 실패: ${String(e).slice(0, 500)}` }, { status: 502 });
  }
}

const openaiTools = [{ type: "function", name: DELEGATION_TOOL, description: TOOL_DESCRIPTION, parameters: TOOL_PARAMETERS }];

async function openaiToken(vad: VadSettings): Promise<VoiceToken> {
  const apiKey = requireEnv("OPENAI_API_KEY");
  const model = process.env.OPENAI_REALTIME_MODEL || "gpt-realtime-2.1";
  // https://developers.openai.com/api/docs/guides/realtime-webrtc
  const r = await fetch("https://api.openai.com/v1/realtime/client_secrets", {
    method: "POST",
    headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      session: {
        type: "realtime",
        model,
        instructions: INSTRUCTIONS,
        audio: {
          input: {
            transcription: { model: process.env.OPENAI_TRANSCRIBE_MODEL || "gpt-4o-transcribe", language: "ko" },
            // 면접 답변은 생각하느라 멈추는 일이 많습니다. 테스트 화면에서 바꿔 보며 기록합니다.
            turn_detection: { type: "server_vad", silence_duration_ms: vad.silenceMs },
          },
          output: { voice: process.env.OPENAI_VOICE || "marin" },
        },
        tools: openaiTools,
        tool_choice: "auto",
      },
    }),
  });
  const data = (await r.json()) as { value?: string; expires_at?: number; error?: unknown };
  if (!r.ok || !data.value) throw new Error(JSON.stringify(data.error ?? data));
  return {
    provider: "openai",
    client_secret: data.value,
    expires_at: new Date((data.expires_at ?? Date.now() / 1000 + 60) * 1000).toISOString(),
    model,
    instructions: INSTRUCTIONS,
    tools: openaiTools,
  };
}

async function geminiToken(vad: VadSettings): Promise<VoiceToken> {
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
          systemInstruction: { parts: [{ text: INSTRUCTIONS }] },
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
    instructions: INSTRUCTIONS,
    tools: [{ functionDeclarations }],
  };
}

function requireEnv(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`${name}가 없습니다 (web/.env.local)`);
  return v;
}
