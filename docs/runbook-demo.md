# 데모 장애 대응 런북

> 해커톤(11/20~21) 데모 중 외부 서비스가 막혔을 때 이 문서대로 전환합니다.
> "상태" 열이 `준비됨`이 아닌 항목은 아직 쓸 수 없습니다. 담당자가 구현하면 상태를 고칩니다.
> 마지막 갱신: 2026-11-17 (B)

## 판단 기준

- 데모 흐름(4분): 로그인·레포 선택·동의 → 캐시된 분석 결과 → 음성 면접 → 하이라이트·변경 이력 → 리포트·이전 비교
- 한 단계에서 **10초 넘게 반응이 없으면** 다음 대비책으로 넘어갑니다. 원인을 그 자리에서 고치지 않습니다.
- 전환은 발표자가 아닌 시연 보조가 맡습니다. 발표자는 "대비 경로로 이어 가겠습니다" 한 문장만 말합니다.

## 실패 지점별 대비책

| 실패 지점 | 증상 | 1차 | 2차 | 최후 | 담당 | 상태 |
|---|---|---|---|---|---|---|
| 판단 LLM | 꼬리질문이 미리 만든 질문으로만 나옴, 로그에 `fallback=timeout/error` | 판단 모델 바꾸기 (아래 A) | `BRAIN_FORCE_PREPARED=true` (아래 B) | 텍스트 모드 + 미리 만든 질문 | B | 1차·2차 준비됨 (서버 API에도 적용), 화면 연결은 로그인(#18) 후. 판단·리포트 LLM은 Gemini만 쓰기로 해서 다른 제공사 대비책은 없음 (#63) |
| 리포트 LLM | 종료 후 리포트가 판단 표(`kind=turn_table`)로 나옴 | 그대로 진행 (판단 표가 대비책) | 리포트 모델을 바꾼 뒤 다음 세션 (아래 A) | | B·D | 판단 표 생성 준비됨, 화면은 D |
| 음성 제공사 (Gemini Live) | 연결 실패, 소리 없음, 자막 멈춤 | 텍스트 모드 | 녹화 영상 | - | C | 1차 구현 (#30): 면접 화면에서 음성 연결이 끊기거나 실패하면 같은 세션으로 텍스트 모드 자동 전환, "텍스트로 바꾸기"로 직접 전환. 소리 없음·자막 멈춤은 자동 감지하지 않으므로 발표자가 직접 전환. 음성 AI는 Gemini만 쓰기로 해서 다른 제공사 대비책은 없음 |
| 분석 | 분석이 끝나지 않음, 실패 | 미리 분석해 둔 시연용 레포를 다시 고름 (`(레포, SHA, 요청자)` 기록 재사용, 바로 끝남) | 미리 분석해 둔 두 번째 레포 | | A | 준비됨 (#12): 같은 레포·커밋·요청자의 끝난 분석을 다시 씀. 분석 요청 API(`POST /api/analyses`)와 레포 선택 화면 연결됨. 배포 DB에는 데모 스냅샷을 넣지 않기로 함 (#19, #109) |
| GitHub App·비공개 레포 | 로그인 실패, 레포 목록 비어 있음 | 다시 로그인, App에 시연용 레포가 설치돼 있는지 확인 | 같은 앱을 설치한 공개 레포 | | D | 준비됨 (#18): 로그인, `/api/repos`, 요청자 토큰으로 분석(#65), `/api/code`. App에 레포를 설치하지 않았으면 목록이 빔. 배포에서는 데모 레포로 시작할 수 없음 (스냅샷 없음, 버튼은 #104에서 감춤) |
| 코드 뷰어 | 코드가 안 뜸 | 경로·함수명·줄 범위만 표시, 음성 계속 | | | C | 미구현 (T8) |
| 현장 네트워크 | 전체 응답 없음 | 모바일 핫스팟 | | 녹화 영상 | 전원 | 핫스팟 기기 확인 필요 |

## 전환 방법

환경 변수는 Railway API 서비스의 Variables에서 바꾸고 재배포합니다. 재배포는 약 1~2분 걸리므로, 기다리는 동안 텍스트 모드로 이어 갑니다.

### A. 판단·분석·리포트 LLM 모델 바꾸기

판단·리포트 LLM은 Gemini를 씁니다 (`LLM_PROVIDER` 기본값 `gemini`, #63).
분석(출제, 검토, 면접 계획)은 BizRouter를 거쳐 OpenAI `gpt-6.2-sol`을 씁니다 (#202).
기본 모델은 판단 `gemini/gemini-3.6-flash`(추론량 `low`, 제한 4초), 리포트 `gemini/gemini-3.8-flash`, 분석 `openai/openai/gpt-6.2-sol`(BizRouter 이름)입니다.
한 모델이 과부하(503)나 한도 초과(429)로 막히면 다른 모델로 바꿉니다. 분석이 막히면 Gemini 키로 넘깁니다.

```text
LLM_JUDGE_MODEL=gemini/gemini-3.8-flash     # 판단 모델이 막혔을 때
LLM_REPORT_MODEL=gemini/gemini-3.6-flash    # 리포트 모델이 막혔을 때
LLM_ANALYSIS_MODEL=gemini/gemini-3.8-flash  # BizRouter나 OpenAI가 막혔을 때 (면접 계획도 같이 바뀜)
```

- 분석 모델을 Gemini로 넘겨도 이미 끝난 분석은 그대로 다시 씁니다. 새로 요청한 분석만 Gemini로 만듭니다.

- 되돌릴 때는 변수를 비웁니다. 비우면 기본 모델을 씁니다.
- 바꾸기 전에 로컬에서 확인합니다: `$env:LLM_JUDGE_MODEL="gemini/gemini-3.8-flash"; uv run python scripts/demo_check.py --real-llm`
- 판단을 3.8 Flash로 바꾸면 느려집니다 (추론량 `low`에서 중앙값 3.8초, 제한 4초에 거의 닿음). 대체 사유 `timeout`이 많으면 B로 갑니다.
- 키 자체가 막혔거나(인증 오류, 결제 한도) Gemini 전체 장애면 모델을 바꿔도 소용없습니다. 바로 B로 갑니다.
- 무료 등급 키로는 판단 호출이 거의 다 실패했습니다 (429, 503). 배포에는 결제를 등록한 키를 넣습니다.
- 지연 측정 기록은 `docs/evals/latency.md`에 있습니다 (2026-11-17, 모델과 추론량별 10회).

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
리포트가 판단 표(`turn_table`)로 자주 나오면 리포트 생성이 제한 30초(`REPORT_TIMEOUT_S`)를 넘는 것입니다. 리포트 모델의 추론량은 `LLM_REPORT_REASONING_EFFORT`(기본 `low`, 12초 안팎)로 바꿉니다. 비우면 모델 기본값(23~28초)을 씁니다.

- [ ] Railway 환경 변수: `LAB_ENABLED=false`, `BRAIN_FORCE_PREPARED=false`, `LLM_PROVIDER`·`LLM_JUDGE_MODEL`·`LLM_REPORT_MODEL` 비움, `LLM_ANALYSIS_MODEL`·`OPENAI_API_KEY`·`OPENAI_API_BASE`는 BizRouter 값 (`docs/deploy.md`)
- [ ] Railway 시작 명령이 `uvicorn --factory gitinsight_worker.server:app_factory`인지 확인 (`gitinsight_api.main:app_factory`이면 분석 요청이 503)
- [ ] `GEMINI_API_KEY`를 Railway(판단·리포트)와 Vercel(음성 토큰)에 등록, 결제 한도 확인 (예산 상한 $100)
- [ ] BizRouter 키(`OPENAI_API_KEY`)를 Railway(분석)에 등록, 잔액 확인
- [ ] 시연에 쓸 레포를 **시연 계정으로 로그인해서** 배포 주소에서 미리 한 번 분석해 둠. 두 번째 레포도 하나 더 분석해 둠 (A)
  - 서버는 같은 레포·커밋·요청자의 끝난 분석을 다시 씁니다. 시연 때 같은 레포를 고르면 바로 끝납니다
  - 분석은 요청한 본인에게만 열립니다 (#52). 다른 계정으로 분석해 두면 시연 계정에서는 새로 분석합니다
  - 미리 분석한 뒤에는 그 레포에 새 커밋을 올리지 않습니다. 커밋이 바뀌면 새로 분석합니다
  - 지금은 Python 코드가 있는 레포만 분석합니다 (#72)
  - 배포 DB에는 데모 분석 스냅샷을 넣지 않습니다 (#19, #109). 그래서 배포에서는 첫 화면의 "데모 레포로 바로 시작"을 쓸 수 없습니다 (#104)
  - 스냅샷 도구(`seed_demo_analysis.py`, `demo_check.py --from-db`)는 남아 있습니다. 로컬 DB에서 데모 레포로 확인할 때 씁니다. `--user-id`는 로그인할 계정의 GitHub 숫자 id이고(`gh api users/<login> -q .id`), `--analysis-id demo-gitinsight-agent`를 줘야 첫 화면의 데모 시작과 이어집니다
- [ ] 녹화 영상을 발표 노트북에 내려받아 둠 (C)
- [ ] 모바일 핫스팟 연결 확인

## 리허설 기록

음성, 판단 LLM, GitHub를 각각 막은 상태에서 데모를 끝까지 진행하고 결과를 적습니다.

| 회차 | 날짜 | 막은 것 | 사용한 대비책 | 완주 | 걸린 시간 | 문제와 조치 |
|---|---|---|---|---|---|---|
| 1 | | 음성 | | | | |
| 2 | | 판단 LLM | | | | |
| 3 | | GitHub | | | | |
