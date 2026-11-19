/**
 * GitHub App 설치 주소 (#150).
 * 레포 목록(`GET /api/repos`)에는 App을 설치한 레포만 나와서, 처음 온 사용자는 여기서 레포를 연결해야 합니다.
 * App 이름(slug)은 비밀값이 아니고 `github.com/apps/<slug>` 주소에 나오는 이름입니다.
 * 로컬과 배포가 같은 App을 쓰므로(#18) 기본값을 코드에 둡니다 (#159).
 * 다른 App으로 시험할 때만 `NEXT_PUBLIC_GITHUB_APP_SLUG`로 바꿉니다. 빌드할 때 들어가므로 바꾸면 다시 배포합니다.
 */
const DEFAULT_APP_SLUG = "gitinsight-sclp";

const slug = process.env.NEXT_PUBLIC_GITHUB_APP_SLUG?.trim() || DEFAULT_APP_SLUG;

export const APP_INSTALL_URL: string | null = `https://github.com/apps/${encodeURIComponent(slug)}/installations/new`;
