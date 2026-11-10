/**
 * OpenAI Realtime 어댑터 (WebRTC).
 * 브라우저가 OpenAI와 직접 연결하고, 이벤트는 데이터 채널 "oai-events"로 주고받습니다.
 * 끼어들기는 OpenAI 서버 VAD가 처리합니다 (사용자가 말하면 AI 음성이 멈춤).
 * https://developers.openai.com/api/docs/guides/realtime-webrtc
 */
import type { Connect } from "./types";

export const connectOpenAI: Connect = async (token, hooks) => {
  const ctx = new AudioContext();
  const pc = new RTCPeerConnection();
  const audioEl = new Audio();
  audioEl.autoplay = true;
  let ai: AnalyserNode | null = null;

  pc.ontrack = (e) => {
    const stream = e.streams[0];
    audioEl.srcObject = stream;
    ai = ctx.createAnalyser();
    ai.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(ai); // 측정만 합니다. 재생은 audioEl
  };

  const mic = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  pc.addTrack(mic.getAudioTracks()[0], mic);
  const user = ctx.createAnalyser();
  user.fftSize = 1024;
  ctx.createMediaStreamSource(mic).connect(user);

  const dc = pc.createDataChannel("oai-events");
  const send = (ev: object) => {
    if (dc.readyState === "open") dc.send(JSON.stringify(ev));
  };
  const opened = new Promise<void>((resolve) => (dc.onopen = () => resolve()));

  dc.onmessage = (msg) => {
    const ev = JSON.parse(String(msg.data)) as { type: string } & Record<string, unknown>;
    switch (ev.type) {
      case "conversation.item.input_audio_transcription.completed":
        if (typeof ev.transcript === "string" && ev.transcript.trim()) hooks.onUserTranscript(ev.transcript.trim());
        break;
      case "response.output_audio_transcript.done":
        if (typeof ev.transcript === "string") hooks.onAiTranscript(ev.transcript, false);
        break;
      case "response.function_call_arguments.done":
        hooks.onToolCall(String(ev.call_id), String(ev.name), parseArgs(ev.arguments));
        break;
      case "error":
        hooks.onError("openai_error", JSON.stringify(ev.error ?? ev).slice(0, 300));
        break;
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
      send({ type: "response.create" });
    },
    respondTool(callId, _name, output) {
      send({
        type: "conversation.item.create",
        item: { type: "function_call_output", call_id: callId, output: JSON.stringify(output) },
      });
      send({ type: "response.create" });
    },
    userAnalyser: () => user,
    aiAnalyser: () => ai,
    stop() {
      dc.close();
      pc.close();
      mic.getTracks().forEach((t) => t.stop());
      audioEl.srcObject = null;
      void ctx.close();
    },
  };
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
