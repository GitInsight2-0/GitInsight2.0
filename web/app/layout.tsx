import { IBM_Plex_Sans_KR, JetBrains_Mono } from "next/font/google";
import "./globals.css";

export const metadata = { title: "GitInsight" };

// 글꼴은 빌드할 때 받아서 우리 서버에서 함께 내보냅니다 (현장 네트워크가 나빠도 Google Fonts를 기다리지 않음).
// 한글 글꼴은 글자 범위별로 나뉘어 있어 미리 받지(preload) 않고, 쓰는 글자만 그때 불러옵니다.
const sans = IBM_Plex_Sans_KR({
  weight: ["400", "500", "600", "700"],
  display: "swap",
  preload: false,
  variable: "--gi-font-plex",
});
const mono = JetBrains_Mono({
  weight: ["400", "500"],
  subsets: ["latin"],
  display: "swap",
  variable: "--gi-font-jetbrains",
});

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" className={`${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
