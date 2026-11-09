---
name: issue-to-pr
description: 진료한장 저장소에서 기능 작업을 이슈 생성부터 브랜치, 커밋, PR까지 팀 규칙대로 진행합니다. 새 기능, 버그 수정, 문서 변경을 시작하거나 커밋 또는 PR을 만들 때 사용합니다.
---

# 이슈에서 PR까지

코드 규칙과 폴더 구조는 루트 AGENTS.md를 따릅니다. 이 스킬은 GitHub 협업 절차만 다룹니다.

## 1. 이슈

- 작업 전에 관련 이슈가 있는지 `gh issue list`로 확인합니다.
- API를 연결하는 작업이면 AGENTS.md의 참고 문서에 있는 Notion API 목록을 먼저 확인하고, 이슈의 참고 문서에 링크를 넣습니다.
- 없으면 `.github/ISSUE_TEMPLATE/issue.md` 형식으로 `gh issue create`를 실행합니다.
- 제목은 `[fe] 요약`처럼 범위를 앞에 붙입니다.
- 라벨(`--label`)과 담당자(`--assignee`)를 반드시 지정합니다. 값을 모르면 사용자에게 묻습니다.

## 2. 브랜치

- main에서 이슈 번호를 포함한 브랜치를 만듭니다.
- 형식: `feat/123-short-name`, `fix/123-short-name`, `docs/123-short-name`.

## 3. 커밋

- 이모지 없이 `작업 유형: 요약` 형식으로 작성합니다.
- 작업 유형: Feat, Fix, Refactor, Style, Docs, Test, Chore.
- 예: `Feat: 진료 요약 화면 추가`, `Fix: 날짜 표시 수정`.
- 커밋 전에 `npm run format`, `npm run lint`, `npm run typecheck`, `npm run test`, `npm run build`를 실행하고 모두 통과해야 합니다.

## 4. PR

- PR을 만들기 전에 CONTRIBUTING.md의 "PR 전 자기 검토 체크리스트"에서 변경 범위에 해당하는 항목을 확인합니다. 확인하지 못한 항목은 PR 본문의 "확인하지 못한 것"에 적습니다.
- `.github/pull_request_template.md` 형식으로 본문을 채워 `gh pr create`를 실행합니다.
- 제목은 커밋 규칙과 같은 형식을 사용합니다.
- 본문에 `Closes #이슈번호`를 넣습니다.
- 라벨, 담당자, 리뷰어를 지정합니다. 리뷰어를 모르면 사용자에게 묻습니다.
- UI 변경이 있으면 스크린샷을 첨부해야 한다고 사용자에게 알립니다.
- 머지 전에 최소 1명의 승인과 CI 통과가 필요하고, Cloudflare Workers 미리보기 URL에서 화면과 주요 경로를 확인해야 한다고 안내합니다.

## 5. 마무리 점검

- [ ] 이슈에 라벨과 담당자가 있는가?
- [ ] 브랜치 이름에 이슈 번호가 있는가?
- [ ] 커밋 메시지에 이모지가 없고 작업 유형이 있는가?
- [ ] 새 컴포넌트가 공용(`src/components`)과 페이지 전용(`src/pages/[pageName]/components`) 중 올바른 위치에 있는가?
- [ ] PR에 라벨, 담당자, 리뷰어가 지정됐는가?
