# 데모 장애 대응 런북

> 해커톤(11/20~21) 데모 중 외부 서비스가 막혔을 때 이 문서대로 전환합니다.
> "상태" 열이 `준비됨`이 아닌 항목은 아직 쓸 수 없습니다. 담당자가 구현하면 상태를 고칩니다.
> 마지막 갱신: 2026-11-10 (B)

## 판단 기준

- 데모 흐름(4분): 로그인·레포 선택·동의 → 캐시된 분석 결과 → 음성 면접 → 하이라이트·변경 이력 → 리포트·이전 비교
- 한 단계에서 **10초 넘게 반응이 없으면** 다음 대비책으로 넘어갑니다. 원인을 그 자리에서 고치지 않습니다.
- 전환은 발표자가 아닌 시연 보조가 맡습니다. 발표자는 "대비 경로로 이어 가겠습니다" 한 문장만 말합니다.

## 실패 지점별 대비책

| 실패 지점 | 증상 | 1차 | 2차 | 최후 | 담당 | 상태 |
|---|---|---|---|---|---|---|
| 판단 LLM | 꼬리질문이 미리 만든 질문으로만 나옴, 로그에 `fallback=timeout/error` | `LLM_PROVIDER` 전환 (아래 A) | `BRAIN_FORCE_PREPARED=true` (아래 B) | 텍스트 모드 + 미리 만든 질문 | B | 1차·2차 준비됨, 서버 연결은 Task 10 후 |
| 리포트 LLM | 종료 후 리포트가 판단 표(`kind=turn_table`)로 나옴 | 그대로 진행 (판단 표가 대비책) | `LLM_PROVIDER` 전환 후 다음 세션 | | B·D | 판단 표 생성 준비됨, 화면은 D |
| 음성 제공사 | 연결 실패, 소리 없음, 자막 멈춤 | 다른 제공사 어댑터 | 텍스트 모드 | 녹화 영상 | C | 미구현 (T3·T13) |
| 분석 | 분석이 끝나지 않음, 실패 | `(레포, SHA)` 캐시 | DB에 넣어 둔 데모 분석 스냅샷 | | A | 미구현 (T6·T11) |
| GitHub App·비공개 레포 | 로그인 실패, 레포 목록 비어 있음 | 같은 앱으로 공개 데모 레포 | 스냅샷 | | D | 미구현 (T4) |
| 코드 뷰어 | 코드가 안 뜸 | 경로·함수명·줄 범위만 표시, 음성 계속 | | | C | 미구현 (T8) |
| 현장 네트워크 | 전체 응답 없음 | 모바일 핫스팟 | | 녹화 영상 | 전원 | 핫스팟 기기 확인 필요 |

## 전환 방법

환경 변수는 Railway API 서비스의 Variables에서 바꾸고 재배포합니다. 재배포는 약 1~2분 걸리므로, 기다리는 동안 텍스트 모드로 이어 갑니다.

### A. 판단·리포트 LLM 제공사 전환

```text
LLM_PROVIDER=gemini        # 되돌릴 때는 openai
```

- 모델 이름 변수(`LLM_JUDGE_MODEL` 등)를 따로 넣어 두었다면 그 값이 우선합니다. 데모 전에는 비워 둡니다.
- 두 제공사의 키(`OPENAI_API_KEY`, `GEMINI_API_KEY`)가 모두 등록되어 있어야 합니다.
- 전환 전에 로컬에서 확인합니다: `$env:LLM_PROVIDER="gemini"; uv run python scripts/demo_check.py --real-llm`
- [미확인] Gemini 판단 모델의 지연과 구조화 출력 동작은 아직 측정하지 않았습니다. 리허설 전에 `scripts/llm_latency.py --model gemini/gemini-3.8-flash`로 측정합니다.

### B. LLM 없이 진행

```text
BRAIN_FORCE_PREPARED=true
```

- 면접 계획, 답변 판단, 리포트에서 LLM을 부르지 않습니다.
- 질문은 주제별 기본 질문과 미리 만든 꼬리질문 2개로 나가고, 리포트는 턴별 판단 표로 나옵니다.
- 로컬 확인: `$env:BRAIN_FORCE_PREPARED="true"; uv run python scripts/demo_check.py`

### C. 텍스트 모드

- 면접 화면의 모드 전환으로 텍스트 입력으로 바꿉니다. 음성과 같은 면접 두뇌 API를 씁니다.
- [미구현] 화면 전환 버튼은 C의 T8에서 만듭니다.

## 데모 전 점검 (당일 아침, 리허설 전)

```text
uv run python scripts/demo_check.py               # LLM 없이 완주
uv run python scripts/demo_check.py --block-llm   # 판단 LLM 오류 상황에서 완주
uv run python scripts/demo_check.py --real-llm    # 실제 LLM으로 완주 (비용 발생)
```

세 명령 모두 마지막 줄까지 `[통과]`여야 합니다. `--real-llm`에서 대체 사유가 `timeout`으로 많이 나오면 `JUDGE_TIMEOUT_S`나 `LLM_JUDGE_REASONING_EFFORT`를 조정합니다.

- [ ] Railway 환경 변수: `LAB_ENABLED=false`, `BRAIN_FORCE_PREPARED=false`, `LLM_PROVIDER=openai`, 모델 이름 변수 비움
- [ ] 두 제공사 API 키 등록, 결제 한도 확인 (예산 상한 $100)
- [ ] 데모 레포 분석이 캐시되어 있는지 확인 (A)
- [ ] 녹화 영상을 발표 노트북에 내려받아 둠 (C)
- [ ] 모바일 핫스팟 연결 확인

## 리허설 기록

음성, 판단 LLM, GitHub를 각각 막은 상태에서 데모를 끝까지 진행하고 결과를 적습니다.

| 회차 | 날짜 | 막은 것 | 사용한 대비책 | 완주 | 걸린 시간 | 문제와 조치 |
|---|---|---|---|---|---|---|
| 1 | | 음성 | | | | |
| 2 | | 판단 LLM | | | | |
| 3 | | GitHub | | | | |
