/**
 * GitInsight 로고 (개편 #125).
 * 커밋 세 개와 갈래를 그린 임시 표시입니다. 정식 로고 파일이 생기면 이 파일만 바꿉니다.
 */
export default function Logo({ size = 26, className }: { size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 26 26" aria-hidden="true">
      <circle cx="7" cy="7" r="3" fill="var(--gi-accent)" />
      <circle cx="7" cy="19" r="3" fill="var(--gi-ink)" />
      <circle cx="19" cy="13" r="3" fill="var(--gi-ink)" />
      <path
        d="M7 10v6M9.5 8.2c4 1 6.5 2.3 7.6 3.4"
        stroke="var(--gi-ink)"
        strokeWidth="1.8"
        fill="none"
        strokeLinecap="round"
      />
    </svg>
  );
}
