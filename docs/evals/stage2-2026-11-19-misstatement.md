# 2단계 틀린 말 생성 평가 (2026-11-19)

- 이슈: #142 (#133의 A 몫)
- 모델: `gemini/gemini-3.8-flash (reasoning_effort=low)`
- 명령: `uv run --env-file .env python scripts/eval_stage2.py [--repo ... --commit ...]`
- 요청자를 주지 않아 기여 라벨은 모두 `unverified`입니다. 틀린 말 고르기는 기여 라벨을 쓰지 않습니다.

| 레포 | 커밋 | 주제 | 틀린 말 | LLM 호출 | 비용 |
|---|---|---|---|---|---|
| `tttae210-sys/gitinsight_agent` (시연, Python) | `6a3111c` | 6 | 6 (absent_call 5, inverted_point 1) | 6회, 재요청 0 | $0.0396 |
| `GitInsight2-0/GitInsight2.0` (Python + TS/TSX) | `fd39d02` | 6 | 6 (absent_call 6) | 6회, 재요청 0 | $0.0464 |

틀린 말을 같은 요청에서 받으므로 호출 수는 늘지 않았습니다. 틀린 말 검증 실패도 0건입니다.

## 왜 돌렸나

#142에서 주제마다 면접관이 할 틀린 말(`Topic.misstatement`)을 만들게 했습니다.
틀린 말이 정말 틀렸는지(맞는 말을 틀린 말로 내놓지 않는지), 그리고 정답 문장만 들어도 정정으로 들리는지를 사람이 코드와 대조했습니다.
#128과 같은 팀 레포 커밋을 써서, TS/JS 주제에서도 고르기가 되는지 봤습니다.

## 사람이 확인한 결과 (A, 코드와 대조)

**12개 모두 정말 틀린 말입니다.** 맞는 말을 틀린 말로 내놓은 것은 없습니다.

- absent_call 11개: 고른 함수 이름이 주제 파일에 한 번도 나오지 않는 것을 `grep -c`로 다시 확인했습니다 (서버 규칙과 같은 조건)
- inverted_point 1개: 시연 레포 `frontend/ui.py::render_highlighted_code`
  - 말: "전달받은 시작 줄과 끝 줄 번호는 별도의 유효성 검사 없이 바로 정수로 취급되어 슬라이싱에 사용되죠?"
  - 코드: `try: int(start_line) ... except ValueError: s_line, e_line = None, None`. 틀린 말이 맞고, 정답 문장("try 구문을 통해 정수 캐스팅을 시도하고 실패 시 None으로 처리합니다")도 코드와 같습니다
  - 이 방식은 서버가 틀렸다는 것을 보장하지 못합니다. 이번 1건은 맞았지만 표본이 적습니다

**면접 질문으로 특히 좋은 것**: 함수가 간접적으로 불리거나 같은 모듈의 다른 것을 써서, 코드를 정확히 알아야 짚을 수 있습니다.

| 주제 | 틀린 말의 함수 | 실제 |
|---|---|---|
| `InterviewBrain` | `validate_judgment` | 직접 부르지 않고 `judge_answer` 안에서 불립니다 |
| `Settings` | `fuzzy_alignment` | `quote_matcher`가 돌려주는 매처 안에서 불립니다 |
| `startVoiceSession` | `buildInstructions` | 같은 `config` 모듈에서 `DELEGATION_TOOL`, `sayMessage`는 가져다 쓰지만 `buildInstructions`는 쓰지 않습니다 |
| `VoiceLab` | `postTurn` | 직접 부르지 않고, VoiceLab이 부르는 `startVoiceSession` 안에서 불립니다 |

**정답 문장**: 12개 모두 "방금 말씀드린 것과 달리"로 시작하고, absent_call은 함수 이름이 들어 있어 그 문장만 들어도 무엇을 바로잡는지 알 수 있습니다.

**말투**: 대부분 "~죠?"로 확인하는 말투입니다. 1건(`extract_question_pool`)은 "~나요?"로 물어 확인보다는 질문에 가깝지만, 틀린 내용을 전제로 하는 것은 같습니다.

## 고르기 규칙을 바꾼 이유 (평가 중 발견)

처음에는 같은 폴더 > 같은 최상위 폴더 > 나머지 순으로, 같은 순위면 경로 순서로 골랐습니다. LLM 없이 고르기만 돌려 보니 그럴듯하지 않은 짝이 나왔습니다.

- `InterviewScreen` → `GrowthChart.tsx`의 `levelOf`, `GrowthTile` → `noop`
- 시연 레포 `frontend/ui.py`의 화면 함수 → `app/agents/`의 질문 추출 함수

그래서 아래처럼 바꿨고, 위 결과는 바꾼 규칙으로 돌린 것입니다.
- 주제 파일이 import하는 모듈의 함수를 먼저 고릅니다 (예: `InterviewBrain`은 `judge` 모듈을 import하지만 `validate_judgment`는 쓰지 않음)
- 같은 순위면 긴 함수를 먼저 고릅니다. 이름이 6자 미만이면 고르지 않습니다
- import하지도 않고 최상위 폴더도 다른 함수는 고르지 않고 inverted_point로 넘깁니다

## 남은 것

- **같은 함수가 여러 주제에 골라짐**: 시연 레포는 absent_call 5개 중 4개가 `extract_question_pool`입니다. 같은 폴더에서 가장 긴 함수라서입니다. 면접 한 번에 틀린 말은 한 번뿐이라 지금은 문제가 없지만, 다시 보기 면접 등으로 여러 번 하게 되면 주제마다 다르게 고르는 규칙이 필요합니다
- **inverted_point 검증**: 서버가 틀렸다는 것을 보장하지 못합니다. 쓰이는 경우가 적지만(이번 12개 중 1개), 더 많은 레포에서 확인이 필요합니다
- **시연 스냅샷**: `fixtures/analysis/gitinsight_agent_live`에는 틀린 말이 없습니다. 시연에서 틀린 말을 보이려면 스냅샷을 다시 만들어야 합니다
