"use client";

import { useEffect, useRef } from "react";
import type { AudioLevels } from "@/lib/voice/session";
import styles from "./interview.module.css";

/** 막대 수와 높이 (px) */
const BARS = 13;
const MIN_H = 8;
const MAX_H = 72;
/** 이 RMS에서 막대가 가득 찹니다. 말소리는 보통 0.02~0.2입니다 */
const FULL_RMS = 0.15;
/** 소리가 없을 때 보여 줄 모양 (가운데가 높은 낮은 물결) */
const IDLE = Array.from({ length: BARS }, (_, i) => MIN_H + Math.round(10 * Math.sin((Math.PI * (i + 1)) / (BARS + 1))));

/**
 * 마이크와 AI 목소리 크기를 막대로 그립니다.
 * 최근 소리 크기를 오른쪽에서 왼쪽으로 흘려 보여 줍니다.
 * 매 프레임 React 상태를 바꾸지 않고 막대 높이만 직접 고칩니다.
 */
export default function Waveform({ levels, active }: { levels: AudioLevels | null; active: boolean }) {
  const bars = useRef<(HTMLSpanElement | null)[]>([]);

  useEffect(() => {
    const paint = (heights: number[]) =>
      heights.forEach((h, i) => {
        const el = bars.current[i];
        if (el) el.style.height = `${h}px`;
      });
    if (!levels || !active || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      paint(IDLE);
      return;
    }
    const history = [...IDLE];
    let buf: Float32Array<ArrayBuffer> | null = null;
    let raf = 0;
    let last = 0;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      // 30fps면 충분합니다.
      if (now - last < 33) return;
      last = now;
      const level = Math.max(rms(levels.user(), buf), rms(levels.ai(), buf));
      const h = MIN_H + Math.round(Math.min(1, Math.sqrt(level / FULL_RMS)) * (MAX_H - MIN_H));
      history.shift();
      history.push(h);
      // 가운데가 가장 최근 소리가 되도록 좌우 대칭으로 그립니다.
      const mid = Math.floor(BARS / 2);
      paint(history.map((_, i) => history[BARS - 1 - Math.abs(i - mid) * 2] ?? MIN_H));
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);

    function rms(node: AnalyserNode | null, prev: Float32Array<ArrayBuffer> | null): number {
      if (!node) return 0;
      if (!prev || prev.length !== node.fftSize) buf = prev = new Float32Array(node.fftSize);
      node.getFloatTimeDomainData(prev);
      let s = 0;
      for (let i = 0; i < prev.length; i++) s += prev[i] * prev[i];
      return Math.sqrt(s / prev.length);
    }
  }, [levels, active]);

  return (
    <span className={styles.wave} aria-hidden="true">
      {IDLE.map((h, i) => (
        <span
          key={i}
          ref={(el) => {
            bars.current[i] = el;
          }}
          className={styles.bar}
          style={{ height: h }}
        />
      ))}
    </span>
  );
}
