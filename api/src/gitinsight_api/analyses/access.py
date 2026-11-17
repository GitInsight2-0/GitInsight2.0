"""분석 요청자의 레포 접근 정보 (#18 2단계).

워커는 이 정보로 요청자 본인의 GitHub 토큰을 써서 레포를 내려받고, 요청자 이메일로 기여 라벨을 붙입니다.
메모리에서만 넘기며 DB, 분석 기록, 로그에 남기지 않습니다. 서버가 다시 시작되면 진행 중 분석은
recover()가 실패로 닫으므로 토큰을 저장해 둘 필요가 없습니다.
"""

from dataclasses import dataclass, field

from pydantic import SecretStr


@dataclass(frozen=True)
class RepoAccess:
    # GitHub App 사용자 토큰입니다. SecretStr이라 repr과 로그에 나오지 않습니다.
    token: SecretStr
    # GitHub 숫자 사용자 ID와 login입니다. noreply 커밋 이메일을 만드는 데 씁니다.
    github_id: int
    login: str
    # GitHub에서 인증된 이메일입니다. App에 이메일 읽기 권한이 없으면 비어 있습니다.
    emails: tuple[str, ...] = field(default=(), repr=False)
