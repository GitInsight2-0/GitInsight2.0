# 진료한장 프로젝트 지침

이 파일을 저장소의 기본 개발 지침으로 사용합니다. 작업을 시작하기 전에 관련 지침과 기존 코드를 확인하고, 범위를 벗어나는 기능이나 화면을 임의로 추가하지 않습니다.

## 기술 기준

- React와 TypeScript를 Vite에서 실행합니다.
- Tailwind CSS 4를 @tailwindcss/vite 플러그인으로 사용합니다.
- 패키지 관리자는 npm입니다. Node.js 22.12 이상을 사용합니다.
- 배포 플랫폼은 Cloudflare Workers입니다. 정적 파일은 Workers Static Assets로 제공하고, SPA 경로 fallback은 wrangler.jsonc의 `not_found_handling: "single-page-application"` 설정으로 처리하므로 public/404.html을 추가하지 않습니다. `/api/*` 요청은 `run_worker_first` 설정으로 fallback보다 먼저 worker/index.ts가 받아 `API_ORIGIN` 백엔드로 프록시합니다.
- 라우팅은 react-router를 사용하고, 경로는 src/App.tsx에서 관리합니다.
- src 내부 import는 @/ 경로 별칭을 사용합니다.
- 프로젝트 요구사항에 없는 API, 로그인, 사용자 데이터 구조는 임의로 만들지 않습니다.

## 코드 규칙

- 문자열에는 큰따옴표를, 문장에는 세미콜론을 사용합니다. 포맷은 Prettier가 강제하며, Tailwind 클래스 순서도 Prettier 플러그인이 정렬합니다.
- 한 줄에는 한 문장만 작성하고, 읽기 쉬운 줄바꿈을 유지합니다.
- 변수와 함수는 camelCase, React 컴포넌트와 생성자 함수는 PascalCase로 작성합니다.
- TypeScript의 strict 설정을 유지하고, 불가피한 경우가 아니면 any를 사용하지 않습니다.
- 공통 디자인 토큰은 src/styles/theme.css의 CSS 변수로 관리합니다.
- 컴포넌트의 레이아웃과 스타일에는 Tailwind 유틸리티를 우선 사용합니다.
- 전역 초기화는 src/styles/base.css(Tailwind preflight와 겹치지 않는 차이만), 글꼴 설정은 src/styles/fonts.css의 --font-sans, 재사용 유틸리티는 src/styles/utilities.css에 둡니다.
- 공유 컴포넌트는 src/components에, 페이지 전용 컴포넌트는 src/pages/[pageName]/components에 둡니다.

## 작업 흐름

- 저장소에서 기능 작업을 할 때는 먼저 담당자와 라벨을 지정한 GitHub 이슈를 준비하고, 이슈 단위 브랜치에서 작업합니다.
- 커밋 메시지는 이모지 없이 "작업 유형: 요약" 형식으로 작성합니다. 예: Feat: 진료 화면 추가, Fix: 날짜 표시 수정, Docs: 개발 안내 보완.
- PR에는 변경 요약, 확인한 항목, UI 변경 시 화면 캡처를 포함합니다. 적절한 라벨과 담당자, 리뷰어를 지정합니다.
- PR을 열기 전에 CONTRIBUTING.md의 "PR 전 자기 검토 체크리스트"를 확인합니다.
- 배포는 Cloudflare Workers Builds Git 연동으로 관리합니다. 빌드 명령은 `npm run build`, 배포 명령은 `npx wrangler deploy`이고, 프로덕션이 아닌 브랜치는 기본값 `npx wrangler versions upload`로 미리보기 버전을 올립니다. GitHub Actions는 format, lint, typecheck, test, build를 확인합니다. 브랜치와 PR의 미리보기 URL도 리뷰에 활용합니다.

## 참고 문서

- API 목록(Notion): https://app.notion.com/p/405169f76f9f804b8dd7e52aea15d932
- 디자인(Figma 모바일 화면): https://www.figma.com/design/B8SyYNgeBYqoH2PbDTtN3E/?node-id=1-2
- API를 연결하는 작업은 시작하기 전에 Notion API 목록을 먼저 확인합니다.

## 명령

- 개발 서버: npm run dev
- 정적 검사: npm run lint
- 포맷 적용 / 확인: npm run format / npm run format:check
- 테스트: npm run test
- 타입 검사: npm run typecheck
- 프로덕션 빌드: npm run build
- Worker 런타임으로 빌드 결과 실행: npm run preview:worker
