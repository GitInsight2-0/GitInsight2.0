/**
 * OpenAI Realtime 어댑터 (WebRTC).
 * 브라우저가 OpenAI와 직접 연결하고, 이벤트는 데이터 채널 "oai-events"로 주고받습니다.
 * 끼어들기는 OpenAI 서버 VAD가 처리합니다 (사용자가 말하면 AI 음성이 멈춤).
 * https://developers.openai.com/api/docs/guides/realtime-webrtc
 */
import type { Connect } from "./types";

/** 데이터 채널이 열릴 때까지 기다리는 시간 */
const OPEN_TIMEOUT_MS = 10_000;

export const connectOpenAI: Connect = async (token, hooks) => {
  const ctx = new AudioContext();
  const pc = new RTCPeerConnection();
  const audioEl = new Audio();
  audioEl.autoplay = true;
  let mic: MediaStream | null = null;
  let ai: AnalyserNode | null = null;
  let dc: RTCDataChannel | null = null;

  // 연결 도중 실패해도, 정상 종료할 때도 같은 정리를 합니다 (마이크가 켜진 채 남지 않게).
  let closed = false;
  const cleanup = () => {
    if (closed) return;
    closed = true;
    dc?.close();
    pc.close();
    mic?.getTracks().forEach((t) => t.stop());
    audioEl.srcObject = null;
    void ctx.close();
  };

  try {
    pc.ontrack = (e) => {
      const stream = e.streams[0];
      audioEl.srcObject = stream;
      ai = ctx.createAnalyser();
      ai.fftSize = 1024;
      ctx.createMediaStreamSource(stream).connect(ai); // 측정만 합니다. 재생은 audioEl
    };

    mic = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    pc.addTrack(mic.getAudioTracks()[0], mic);
    const user = ctx.createAnalyser();
    user.fftSize = 1024;
    ctx.createMediaStreamSource(mic).connect(user);

    const channel = pc.createDataChannel("oai-events");
    dc = channel;
    const send = (ev: object) => {
      if (channel.readyState === "open") channel.send(JSON.stringify(ev));
    };

    // 응답이 진행 중일 때 response.create를 보내면 conversation_already_has_active_response 오류가 납니다.
    // 진행 중이면 미뤄 두었다가 response.done 뒤에 보냅니다.
    let responseActive = false;
    let responsePending = false;
    const requestResponse = () => {
      if (responseActive) {
        responsePending = true;
        return;
      }
      responseActive = true;
      send({ type: "response.create" });
    };

    const opened = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("OpenAI 데이터 채널이 열리지 않았습니다")), OPEN_TIMEOUT_MS);
      channel.onopen = () => {
        clearTimeout(timer);
        resolve();
      };
    });

    channel.onmessage = (msg) => {
      const ev = JSON.parse(String(msg.data)) as { type: string } & Record<string, unknown>;
      switch (ev.type) {
        case "response.created":
          responseActive = true;
          break;
        case "response.done":
          responseActive = false;
          if (responsePending) {
            responsePending = false;
            requestResponse();
          }
          break;
        case "conversation.item.input_audio_transcription.completed":
          if (typeof ev.transcript === "string" && ev.transcript.trim()) hooks.onUserTranscript(ev.transcript.trim());
          break;
        case "response.output_audio_transcript.done":
          if (typeof ev.transcript === "string") hooks.onAiTranscript(ev.transcript, false);
          break;
        case "response.function_call_arguments.done":
          hooks.onToolCall(String(ev.call_id), String(ev.name), parseArgs(ev.arguments));
          break;
        case "error": {
          const err = (ev.error ?? {}) as { code?: unknown };
          if (err.code === "conversation_already_has_active_response") {
            // 미뤄 둔 요청은 response.done 뒤에 다시 보냅니다.
            responsePending = true;
            break;
          }
          hooks.onError("openai_error", JSON.stringify(ev.error ?? ev).slice(0, 300));
          break;
        }
      }
    };

    pc.onconnectionstatechange = () => {
      if (pc.connectionState === "failed") hooks.onError("webrtc_failed", "WebRTC 연결 실패");
      if (pc.connectionState === "closed" || pc.connectionState === "disconnected") hooks.onClosed(pc.connectionState);
    };

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const sdp = await fetch("https://api.openai.com/v1/realtime/calls", {
      method: "POST",
      body: offer.sdp,
      headers: { Authorization: `Bearer ${token.client_secret}`, "Content-Type": "application/sdp" },
    });
    if (!sdp.ok) throw new Error(`SDP 교환 실패 ${sdp.status}: ${(await sdp.text()).slice(0, 300)}`);
    await pc.setRemoteDescription({ type: "answer", sdp: await sdp.text() });
    await opened;

    return {
      say(text) {
        send({
          type: "conversation.item.create",
          item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
        });
        requestResponse();
      },
      respondTool(callId, _name, output) {
        send({
          type: "conversation.item.create",
          item: { type: "function_call_output", call_id: callId, output: JSON.stringify(output) },
        });
        requestResponse();
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

function parseArgs(raw: unknown): Record<string, unknown> {
  if (typeof raw !== "string") return {};
  try {
    const v: unknown = JSON.parse(raw);
    return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}
