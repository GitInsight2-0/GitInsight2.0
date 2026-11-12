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

/** setupComplete를 기다리는 시간 */
const SETUP_TIMEOUT_MS = 10_000;

export const connectGemini: Connect = async (token, hooks, { signal }) => {
  if (signal.aborted) throw abortError();
  let mic: MediaStream | null = null;
  let ws: WebSocket | null = null;
  let worklet: AudioWorkletNode | null = null;
  let inCtx: AudioContext | null = null;
  const outCtx = new AudioContext({ sampleRate: 24000 });

  // ── 출력 재생 ──
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

  // 연결 도중 실패해도, 정상 종료할 때도 같은 정리를 합니다 (마이크가 켜진 채 남지 않게).
  let ready = false;
  let closed = false;
  const cleanup = () => {
    if (closed) return;
    closed = true;
    ready = false;
    if (ws && ws.readyState <= WebSocket.OPEN) ws.close(1000);
    flush();
    worklet?.disconnect();
    mic?.getTracks().forEach((t) => t.stop());
    void inCtx?.close();
    void outCtx.close();
  };

  // 연결 중에 멈추면 기다리던 단계를 바로 끝냅니다.
  const aborted = new Promise<never>((_, reject) => {
    signal.addEventListener(
      "abort",
      () => {
        cleanup();
        reject(abortError());
      },
      { once: true },
    );
  });
  aborted.catch(() => undefined);
  const orAbort = <T,>(p: Promise<T>) => Promise.race([p, aborted]);

  try {
    // ── 마이크 ──
    mic = await orAbort(
      navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      }),
    );
    inCtx = new AudioContext({ sampleRate: 16000 });
    await orAbort(inCtx.audioWorklet.addModule("/pcm-capture-worklet.js"));
    const micSrc = inCtx.createMediaStreamSource(mic);
    const node = new AudioWorkletNode(inCtx, "pcm-capture");
    worklet = node;
    const user = inCtx.createAnalyser();
    user.fftSize = 1024;
    micSrc.connect(node);
    micSrc.connect(user);

    // ── WebSocket ──
    const socket = new WebSocket(`${WS_URL}?access_token=${encodeURIComponent(token.client_secret)}`);
    ws = socket;
    // 바이너리 프레임을 ArrayBuffer로 받아 동기로 해석합니다.
    // Blob.text()를 await하면 메시지 처리 순서가 뒤바뀔 수 있습니다 (오디오 조각, 끼어들기 신호의 순서가 중요).
    socket.binaryType = "arraybuffer";
    const decoder = new TextDecoder();
    const send = (m: object) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(m));
    };

    let userText = "";
    let aiText = "";
    // Gemini는 item id가 없어 사용자 턴 번호를 직접 매깁니다.
    // 도구 호출이 오면 그 턴을 닫고 다음 번호로 넘어갑니다.
    // 도구 호출 뒤 AI 답변 소리가 오기 전에 도착한 받아쓰기 조각은 닫힌 턴(이전 번호)의 늦은 결과로 표시합니다.
    let userTurn = 1;
    let awaitingReply = false;
    const keyOf = (n: number) => `g${n}`;
    const flushUser = () => {
      const t = userText.trim();
      if (t) hooks.onUserTranscript(t, keyOf(awaitingReply ? userTurn - 1 : userTurn));
      userText = "";
    };

    const setupDone = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Gemini 설정 응답(setupComplete)이 오지 않았습니다")), SETUP_TIMEOUT_MS);

      socket.onopen = () => {
        // 지시문·도구·음성 설정은 토큰에 잠겨 있어 모델만 보냅니다.
        send({ setup: { model: `models/${token.model}` } });
      };
      socket.onclose = (e) => {
        clearTimeout(timer);
        if (!ready) reject(new Error(`Gemini 연결 실패 ${e.code} ${e.reason}`));
        hooks.onClosed(`${e.code} ${e.reason}`);
      };
      socket.onmessage = (e: MessageEvent<string | ArrayBuffer>) => {
        const text = typeof e.data === "string" ? e.data : decoder.decode(e.data);
        const m = JSON.parse(text) as GeminiMessage;

        if (m.setupComplete) {
          clearTimeout(timer);
          ready = true;
          resolve();
        }

        const sc = m.serverContent;
        if (sc) {
          if (sc.interrupted) flush();
          const hasAudio = (sc.modelTurn?.parts ?? []).some((p) => p.inlineData?.data);
          // 늦은 조각을 먼저 이전 턴으로 넘긴 뒤, AI 답변이 시작되면 새 턴을 받기 시작합니다.
          if (awaitingReply && sc.inputTranscription?.text) {
            userText += sc.inputTranscription.text;
            flushUser();
            sc.inputTranscription = undefined;
          }
          if (awaitingReply && (hasAudio || sc.interrupted || sc.turnComplete)) awaitingReply = false;
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
          // 이 시점까지 모인 받아쓰기를 먼저 넘겨, 진행기가 도구 인자보다 받아쓰기를 쓰게 합니다.
          flushUser();
          hooks.onToolCall(call.id, call.name, call.args ?? {}, keyOf(userTurn));
          userTurn += 1;
          awaitingReply = true;
        }

        if (m.goAway) hooks.onError("gemini_go_away", `goAway: 남은 시간 ${m.goAway.timeLeft ?? "?"} (재연결 필요)`);
      };
    });
    socket.onerror = () => hooks.onError("ws_error", "Gemini WebSocket 오류");

    await orAbort(setupDone);

    node.port.onmessage = (e: MessageEvent<Float32Array>) => {
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
      stop: cleanup,
    };
  } catch (e) {
    cleanup();
    throw e;
  }
};

function abortError() {
  return new DOMException("연결을 멈췄습니다", "AbortError");
}

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
