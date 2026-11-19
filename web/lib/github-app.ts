/**
 * GitHub App 설치 주소 (#150).
 * 레포 목록(`GET /api/repos`)에는 App을 설치한 레포만 나와서, 처음 온 사용자는 여기서 레포를 연결해야 합니다.
 * App 이름(slug)은 비밀값이 아니고 `github.com/apps/<slug>` 주소에 나오는 이름입니다.
 * `NEXT_PUBLIC_` 변수는 빌드할 때 들어가므로 바꾸면 다시 배포합니다. 없으면 null이고, 화면은 안내 글만 보여 줍니다.
 */
const slug = process.env.NEXT_PUBLIC_GITHUB_APP_SLUG?.trim();

export const APP_INSTALL_URL: string | null = slug
  ? `https://github.com/apps/${encodeURIComponent(slug)}/installations/new`
  : null;
