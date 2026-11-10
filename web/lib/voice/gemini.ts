/**
 * Gemini Live 어댑터 (WebSocket).
 * 입력: 16kHz 16bit PCM base64, 출력: 24kHz 16bit PCM base64 (브라우저에서 직접 재생).
 * 끼어들기: serverContent.interrupted가 오면 재생 중인 소리를 직접 끊습니다.
 * https://ai.google.dev/gemini-api/docs/live-api/get-started-websocket
 */
import type { Connect } from "./types";

// 임시 토큰은 v1alpha 엔드포인트에 access_token으로 붙습니다.
// 문서에 v1beta로 적힌 곳도 있어, 연결이 안 되면 이 값과 토큰 라우트의 apiVersion을 함께 바꿔 봅니다.
const WS_URL =
  "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained";

export const connectGemini: Connect = async (token, hooks) => {
  // ── 출력 재생 ──
  const outCtx = new AudioContext({ sampleRate: 24000 });
  const ai = outCtx.createAnalyser();
  ai.fftSize = 1024;
  ai.connect(outCtx.destination);
  let playhead = 0;
  const playing = new Set<AudioBufferSourceNode>();
  const enqueue = (b64: string) => {
    const pcm = new Int16Array(base64ToBytes(b64).buffer);
    const buf = outCtx.createBuffer(1, pcm.length, 24000);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
    const src = outCtx.createBufferSource();
    src.buffer = buf;
    src.connect(ai);
    playhead = Math.max(playhead, outCtx.currentTime + 0.02);
    src.start(playhead);
    playhead += buf.duration;
    playing.add(src);
    src.onended = () => playing.delete(src);
  };
  const flush = () => {
    playing.forEach((s) => s.stop());
    playing.clear();
    playhead = 0;
  };

  // ── 마이크 ──
  const mic = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  });
  const inCtx = new AudioContext({ sampleRate: 16000 });
  await inCtx.audioWorklet.addModule("/pcm-capture-worklet.js");
  const micSrc = inCtx.createMediaStreamSource(mic);
  const worklet = new AudioWorkletNode(inCtx, "pcm-capture");
  const user = inCtx.createAnalyser();
  user.fftSize = 1024;
  micSrc.connect(worklet);
  micSrc.connect(user);

  // ── WebSocket ──
  const ws = new WebSocket(`${WS_URL}?access_token=${encodeURIComponent(token.client_secret)}`);
  const send = (m: object) => {
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m));
  };
  let ready = false;
  let userText = "";
  let aiText = "";
  const flushUser = () => {
    const t = userText.trim();
    if (t) hooks.onUserTranscript(t);
    userText = "";
  };

  const setupDone = new Promise<void>((resolve, reject) => {
    ws.onopen = () => {
      // 지시문·도구·음성 설정은 토큰에 잠겨 있어 모델만 보냅니다.
      send({ setup: { model: `models/${token.model}` } });
    };
    ws.onclose = (e) => {
      if (!ready) reject(new Error(`Gemini 연결 실패 ${e.code} ${e.reason}`));
      hooks.onClosed(`${e.code} ${e.reason}`);
    };
    ws.onmessage = async (e) => {
      const text = typeof e.data === "string" ? e.data : await (e.data as Blob).text();
      const m = JSON.parse(text) as GeminiMessage;

      if (m.setupComplete) {
        ready = true;
        resolve();
      }

      const sc = m.serverContent;
      if (sc) {
        if (sc.interrupted) flush();
        for (const p of sc.modelTurn?.parts ?? []) if (p.inlineData?.data) enqueue(p.inlineData.data);
        if (sc.inputTranscription?.text) userText += sc.inputTranscription.text;
        if (sc.outputTranscription?.text) aiText += sc.outputTranscription.text;
        if (sc.turnComplete || sc.interrupted) {
          flushUser();
          if (aiText.trim()) hooks.onAiTranscript(aiText.trim(), !!sc.interrupted);
          aiText = "";
        }
      }

      for (const call of m.toolCall?.functionCalls ?? []) {
        flushUser();
        hooks.onToolCall(call.id, call.name, call.args ?? {});
      }

      if (m.goAway) hooks.onError("gemini_go_away", `goAway: 남은 시간 ${m.goAway.timeLeft ?? "?"} (재연결 필요)`);
    };
  });
  ws.onerror = () => hooks.onError("ws_error", "Gemini WebSocket 오류");

  await setupDone;

  worklet.port.onmessage = (e: MessageEvent<Float32Array>) => {
    if (ready) send({ realtimeInput: { audio: { data: floatToPcm16Base64(e.data), mimeType: "audio/pcm;rate=16000" } } });
  };

  return {
    say(text) {
      // 반응이 없으면 realtimeInput: { text } 방식으로 바꿔 봅니다.
      send({ clientContent: { turns: [{ role: "user", parts: [{ text }] }], turnComplete: true } });
    },
    respondTool(callId, name, output) {
      send({ toolResponse: { functionResponses: [{ id: callId, name, response: output }] } });
    },
    userAnalyser: () => user,
    aiAnalyser: () => ai,
    stop() {
      ready = false;
      ws.close(1000);
      flush();
      worklet.disconnect();
      mic.getTracks().forEach((t) => t.stop());
      void inCtx.close();
      void outCtx.close();
    },
  };
};

interface GeminiMessage {
  setupComplete?: object;
  serverContent?: {
    modelTurn?: { parts?: { inlineData?: { data?: string; mimeType?: string } }[] };
    inputTranscription?: { text?: string };
    outputTranscription?: { text?: string };
    turnComplete?: boolean;
    interrupted?: boolean;
  };
  toolCall?: { functionCalls?: { id: string; name: string; args?: Record<string, unknown> }[] };
  goAway?: { timeLeft?: string };
}

function base64ToBytes(b64: string): Uint8Array<ArrayBuffer> {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function floatToPcm16Base64(f: Float32Array): string {
  const pcm = new Int16Array(f.length);
  for (let i = 0; i < f.length; i++) {
    const s = Math.max(-1, Math.min(1, f[i]));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  const bytes = new Uint8Array(pcm.buffer);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}
