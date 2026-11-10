import type { NextConfig } from "next";

// 브라우저는 같은 출처의 /api/*로 부르고, Next가 FastAPI로 넘깁니다 (서버 CORS_ORIGINS를 비워 둘 수 있습니다).
const apiOrigin = process.env.API_ORIGIN ?? "http://localhost:8000";

const config: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }];
  },
};

export default config;
