// 마이크 Float32 샘플을 약 100ms 단위로 모아 메인 스레드로 보낸다.
// AudioContext를 sampleRate 16000으로 만들면 브라우저가 리샘플링해 준다.
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(1600);
    this.len = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.buf[this.len++] = ch[i];
      if (this.len === this.buf.length) {
        this.port.postMessage(this.buf.slice(0));
        this.len = 0;
      }
    }
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
