# 2단계 분석 평가 (2026-11-18)

- 레포: `GitInsight2-0/GitInsight2.0` @ `4cfdc64dd49034a7538ba3a15299e9e4dab06541`
- 모델: `gemini/gemini-3.8-flash (reasoning_effort=low)`
- 주제 수: 6
- 기여 라벨: verified 6개
- LLM 호출: 6회 (검증 실패 재요청 포함), 이 중 스키마 재시도 0회
- 비용: $0.0363
- 호출당 최대 지연: 7961 ms

## 왜 돌렸나

#67(참조 수), #93(다른 파일 정의 요약), #98(주석 표시 검사)의 규칙은 데모 레포 하나로만 평가했습니다.
리뷰(B, D)에서 "다른 레포 하나로 질문이 자연스러운지 보자"고 해서, 팀 저장소(Python, 심볼 1094개, 주제 후보 109개)를 main `4cfdc64`로 분석했습니다.
기여 라벨은 A(sehoon6295) 기준입니다. 커밋 이메일은 명령줄로만 넘겼고 이 기록에는 없습니다.

## 사람이 확인한 결과 (A, 코드와 대조)

- **사실 확인**: 6개 주제의 요약, 요점, 꼬리질문을 main 코드와 대조했습니다. **1건이 코드와 어긋나고, 1건은 이유가 추측입니다** (PR #106 리뷰에서 B가 짚음. 처음 기록에는 "틀린 내용 없음"으로 적었습니다)
  - 어긋남: `git_cloner` p4 "인증 토큰 등 민감 정보가 환경 변수나 에러 추적 정보에 노출되지 않도록 안전하게 분리하여 주입한다".
    실제로는 토큰을 clone 단계의 **환경 변수로 넘깁니다** (`clone_env`, `_auth_env`). 명령줄 인자와 `.git/config`에 남기지 않는 것이 요점이어야 합니다.
    지원자가 "환경 변수로 넘긴다"고 맞게 답해도 판단 LLM이 요점과 반대라고 볼 수 있습니다
  - 이유가 추측: `InMemoryAnalysisStore` p3 "get_record와 get 메서드는 락 없이 메모리에서 바로 읽어 조회 성능을 확보했다".
    락을 쓰지 않는 것은 사실이지만, 성능 때문이라는 근거는 코드에 없습니다
  - 나머지는 코드 그대로입니다 (예: 결과를 `model_dump_json()` 문자열로 보관)
- **고른 주제**: 모두 요청자(A)가 쓴 코드입니다 (`verified` 6개). 껍데기 후보 1개는 LLM 전에 걸러졌습니다
- **새 규칙의 영향**
  - 다른 파일 정의 요약이 붙어도, 맞지 않는 점이 없는 코드라 결함 요점을 지어내지 않았습니다 ("맞지 않는 점이 없으면 요점을 만들지 않는다" 규칙대로)
  - 참조 0인 주제가 없어 "부르는 곳을 찾지 못했다" 문장도 나오지 않았습니다
  - `[A]` 같은 주석 표시, "정의 요약" 같은 내부 용어, 줄 번호가 없습니다. 검증 재요청도 0회입니다
- **아쉬운 점** (막을 정도는 아님)
  - `run_analysis` 꼬리질문 "requester 정보가 없을 때 contribution_lookup의 처리 결과는…"은 전제가 조금 어긋납니다. `contribution_lookup`은 요청자를 직접 보지 않고 1단계 결과를 찾기만 합니다. 답은 할 수 있는 질문입니다
  - 난이도 1 주제의 질문이 "전체 흐름을 설명해 주세요"처럼 넓은 편입니다 (데모 레포에서도 같음)
- **결론**: 데모 레포가 아닌 레포에서도 질문은 자연스럽고 대부분 사실과 맞습니다. 레포 하나, 주제 6개의 결과라 프롬프트를 바꿀 근거로는 약해서 이번에는 바꾸지 않습니다
- **다음 평가에서 볼 것**: 위 두 건은 모두 코드에 보이는 동작 뒤에 의도나 이유를 덧붙이다 어긋난 같은 유형입니다.
  다음 평가에서도 나오면 "요점에는 코드에 보이는 동작만 쓰고 이유는 짐작하지 않는다"는 규칙을 2단계 프롬프트에 넣는 것을 검토합니다

사람이 확인할 것: 기대 포인트가 코드에 실제로 있는가, 질문이 존댓말 한 문장이고 자연스러운가,
힌트가 답을 알려 주지 않는가, 관련 심볼이 꼬리질문과 맞는가.

## Postgres 저장소의 분석 생성 절차 (`api/src/gitinsight_api/analyses/postgres.py::PostgresAnalysisStore`, 난이도 1, 기여 verified)

- 요약: PostgresAnalysisStore는 레포 분석 요청의 상태와 결과를 데이터베이스에서 관리하는 클래스입니다. get_or_create 메서드는 오래된 미완료 분석을 먼저 실패 처리한 후 새 기록을 넣거나 이미 있는 기록을 조회합니다. 이외에도 진행 상태 갱신, 최종 결과 저장, 데모용 스냅샷 저장 등의 작업을 수행합니다.
- 질문: get_or_create 메서드에서 분석 기록을 가져오거나 새로 만들 때 어떤 순서로 처리하나요?
- 취지: 요청된 분석 기록을 데이터베이스에서 찾아보거나 새로 등록할 때 거치는 단계적 절차를 묻는 질문입니다.
- 힌트: get_or_create 메서드 내부에서 트랜잭션을 시작한 뒤 실행하는 SQL 쿼리들의 흐름을 살펴보세요.
- 관련 심볼: api/src/gitinsight_api/analyses/postgres.py::PostgresAnalysisStore.get_or_create, api/src/gitinsight_api/analyses/postgres.py::PostgresAnalysisStore.save_result, api/src/gitinsight_api/analyses/postgres.py::PostgresAnalysisStore.save_snapshot
- 기대 포인트:
  - 현재 시각을 기준으로 stale_after보다 오래 머무른 진행 중 상태의 기록을 먼저 찾아 FAILED 상태와 오류 메시지로 갱신한다. → 새 기록을 삽입하기 전에 진행 중이던 기존 기록들은 어떻게 정리하나요?
  - analyses 테이블에 새로운 QUEUED 상태의 레코드를 삽입하되, 실패 상태가 아닌 동일 키가 이미 있다면 충돌을 무시(on_conflict_do_nothing)한다. → 새 분석 레코드를 삽입할 때 충돌이 발생하면 어떻게 처리하도록 지정되어 있나요?
  - 삽입에 성공하면 생성된 레코드와 True를 반환하고, 충돌로 삽입되지 않았다면 기존 활성 레코드를 조회하여 False와 함께 반환한다. → 삽입을 시도한 결과에 따라 각각 무엇을 반환하게 되나요?
- 꼬리질문:
  - save_result 메서드에서는 분석 결과를 저장하기 전에 어떤 조건들을 확인하고 업데이트를 진행하나요? (api/src/gitinsight_api/analyses/postgres.py::PostgresAnalysisStore.save_result)
  - save_snapshot 메서드는 일반 분석 저장과 비교했을 때 기존 기록들을 어떻게 다루나요? (api/src/gitinsight_api/analyses/postgres.py::PostgresAnalysisStore.save_snapshot)

## 인메모리 분석 스토어 동시성 설계 (`api/src/gitinsight_api/analyses/store.py::InMemoryAnalysisStore`, 난이도 2, 기여 verified)

- 요약: InMemoryAnalysisStore는 분석 작업의 상태와 결과를 인메모리 딕셔너리로 관리하는 저장소 클래스입니다. 비동기 락을 사용하여 중복 작업 생성 방지, 오래된 작업 무효화, 상태 갱신 등의 동시성 제어를 수행합니다. 분석 완료 결과는 JSON 형태로 직렬화하여 저장하고 필요 시 역직렬화하여 반환합니다.
- 질문: 인메모리 분석 저장소에서 비동기 락을 어떻게 활용하고 계신가요?
- 취지: 여러 요청이 동시에 들어올 때 인메모리 데이터의 일관성을 지키기 위해 동시성을 제어한 방식을 묻습니다.
- 힌트: 클래스 내부의 _lock 속성이 어떤 메서드들에서 사용되고 있는지 살펴보세요.
- 관련 심볼: api/src/gitinsight_api/analyses/store.py::InMemoryAnalysisStore.get_or_create, api/src/gitinsight_api/analyses/store.py::InMemoryAnalysisStore.save_result, api/src/gitinsight_api/analyses/store.py::InMemoryAnalysisStore.update_state
- 기대 포인트:
  - get_or_create, update_state, save_result 등 주요 상태 변경 메서드에서 asyncio.Lock을 활용해 동시성 경쟁 상태를 방지한다는 점을 말한다. → 작업을 생성하거나 상태를 바꿀 때 락을 어떻게 사용하셨나요?
  - get_or_create에서 기존 작업이 stale 상태일 때 같은 락 안에서 기존 작업을 실패로 닫아 지연된 이전 워커의 쓰기를 차단한다는 점을 말한다. → 작업이 오래되었을 때 새 작업을 시작하면서 기존 작업은 어떻게 처리하나요?
  - get_record와 get 메서드는 락 없이 메모리에서 바로 읽어 조회 성능을 확보했다는 점을 말한다. → 조회 메서드에서도 락을 사용하도록 구성하셨나요?
- 꼬리질문:
  - get_or_create 안에서 오래된 작업을 실패 처리하는 이유는 무엇인가요? (api/src/gitinsight_api/analyses/store.py::InMemoryAnalysisStore.get_or_create)
  - save_result에서 결과를 JSON 문자열로 직렬화해 보관하도록 설계한 이유는 무엇인가요? (api/src/gitinsight_api/analyses/store.py::InMemoryAnalysisStore.save_result)

## LLM 결과 검증과 예외 처리 (`worker/src/gitinsight_worker/stage2.py::validate_material`, 난이도 3, 기여 verified)

- 요약: LLM이 생성한 모의면접 재료를 Topic 인스턴스로 변환하기 위해 검증을 수행합니다. 질문 텍스트 규칙, 포인트 개수, 허용된 심볼 목록 포함 여부를 확인하고 규칙 위반 시 TopicMaterialError를 발생시킵니다. 마지막으로 Topic 모델을 조립하며 발생하는 ValidationError도 잡아서 TopicMaterialError로 통일해 전달합니다.
- 질문: 생성된 재료를 검증하다 오류가 나면 어떻게 처리되나요?
- 취지: 검증 과정이나 모델 생성 도중 규칙을 벗어났을 때 어떤 오류를 어떻게 상위로 알리는지 묻는 질문입니다.
- 힌트: validate_material 함수 내부에서 problems 리스트에 문제를 모으는 부분과 마지막 try-except 블록을 살펴보세요.
- 관련 심볼: worker/src/gitinsight_worker/stage2.py::TopicMaterial, worker/src/gitinsight_worker/stage2.py::TopicMaterialError
- 기대 포인트:
  - 말투, 금지된 주석/줄번호 표현, 포인트 개수, 허용되지 않은 심볼 ID 등의 문제가 발견되면 수집된 문제 목록을 담아 TopicMaterialError를 발생시킨다. → 텍스트 규칙이나 심볼 검증을 통과하지 못하면 어떤 방식으로 예외가 발생하나요?
  - Topic 객체 생성 중 ValidationError가 발생할 경우 입력값 노출을 막기 위해 오류 경로와 유형만 정제하여 TopicMaterialError로 감싸 다시 던진다. → 최종 객체를 생성하는 과정에서 유효성 검사 에러가 나면 어떻게 대응하고 있나요?
  - 단일 에러 발생 시 즉시 중단하지 않고 problems 배열에 발생 가능한 검증 결함을 모아서 한 번에 전달한다. → 검증 조건을 확인할 때 여러 결함을 어떻게 취합하여 보고하나요?
- 꼬리질문:
  - TopicMaterialError 발생 시 오류 원인을 한 번에 모아서 던지는 방식을 택한 이유가 무엇인가요? (worker/src/gitinsight_worker/stage2.py::TopicMaterialError)
  - ValidationError를 처리할 때 원본 입력을 숨기고 오류 경로와 유형만 남긴 이유는 무엇인가요? (-)

## InProcessLauncher 동작 구조 (`worker/src/gitinsight_worker/jobs.py::InProcessLauncher`, 난이도 1, 기여 verified)

- 요약: InProcessLauncher는 서버 프로세스 내부에서 asyncio 작업으로 분석 작업을 실행하고 관리하는 런처 클래스입니다. 세마포어를 통해 동시 실행 개수를 제어하며, 비동기 태스크 목록을 유지해 작업 대기와 취소를 수행합니다. 또한 서버 시작 시 미완료 분석을 복구하고 종료 시 실행 중인 작업을 취소하는 생명주기 관리 기능을 제공합니다.
- 질문: InProcessLauncher가 분석 작업을 실행하고 관리하는 전체적인 흐름을 설명해 주시겠어요?
- 취지: 클래스가 분석 요청을 받아 작업을 시작하고 동시성을 제어하며 관리하는 전반적인 절차를 묻는 질문입니다.
- 힌트: launch 메서드와 _run 메서드가 태스크를 생성하고 세마포어를 사용하는 부분을 확인해 보세요.
- 관련 심볼: worker/src/gitinsight_worker/jobs.py::InProcessLauncher.launch, worker/src/gitinsight_worker/jobs.py::InProcessLauncher._run, worker/src/gitinsight_worker/jobs.py::InProcessLauncher.aclose
- 기대 포인트:
  - launch 메서드에서 복제기와 요청자 객체를 구한 뒤 asyncio.create_task로 _run 태스크를 만들어 내부 태스크 셋에 보관하고 완료 콜백으로 정리한다. → launch 메서드 안에서 새로운 분석 태스크를 만들고 등록할 때 어떤 단계를 거치나요?
  - _run 메서드에서 asyncio.Semaphore를 사용해 설정된 동시 실행 수 제한 안에서 execute 함수를 호출해 분석을 수행한다. → 동시에 여러 분석 요청이 들어왔을 때 실행 개수를 어떻게 조절하나요?
  - recover로 이전 프로세스의 중단 기록을 정리하고, join과 aclose를 통해 태스크 대기 및 취소 처리를 수행한다. → 서버가 시작되거나 종료될 때 작업 목록과 기존 기록은 어떻게 정리되나요?
- 꼬리질문:
  - launch에서 태스크가 끝났을 때 목록에서 제거하는 처리는 어떤 방식으로 이루어지나요? (worker/src/gitinsight_worker/jobs.py::InProcessLauncher.launch)
  - aclose 메서드에서 실행 중인 태스크들을 취소하고 종료할 때 어떤 절차를 거치나요? (worker/src/gitinsight_worker/jobs.py::InProcessLauncher.aclose)

## 비동기 파이프라인과 정리 설계 (`worker/src/gitinsight_worker/pipeline.py::run_analysis`, 난이도 2, 기여 verified)

- 요약: 임시 디렉터리를 생성해 레포지토리를 클론하고 정적 분석과 LLM 기반 분석을 단계별로 진행합니다. 분석 진행 상황과 단계별 진척도를 보고하며, 성공이나 실패 여부와 무관하게 임시 디렉터리를 정리합니다.
- 질문: 클론이나 분석 작업을 별도 스레드로 위임해 처리하도록 설계하신 이유가 무엇인가요?
- 취지: 비동기 함수 내부에서 동기 작업을 다른 스레드로 실행하도록 분리한 설계 의도를 묻습니다.
- 힌트: run_analysis 내부에서 asyncio.to_thread로 감싸진 함수 호출들과 finally 구문을 살펴보세요.
- 관련 심볼: worker/src/gitinsight_worker/pipeline.py::_state, worker/src/gitinsight_worker/pipeline.py::contribution_lookup
- 기대 포인트:
  - clone, analyze_tree, remove_tree 같은 블로킹 I/O 및 CPU 연산이 이벤트 루프를 막지 않도록 asyncio.to_thread를 사용함을 설명한다. → to_thread를 쓰지 않고 동기 함수를 직접 호출하면 비동기 파이프라인에서 어떤 영향이 생길까요?
  - stage1에서 지원 언어의 심볼이 없을 때 LLM 호출을 건너뛰고 NoSupportedCodeError를 발생시켜 불필요한 비용과 대기 시간을 줄이도록 설계한 점을 설명한다. → stage1 결과에서 symbols가 비어 있을 때 즉시 예외를 발생시키는 설계는 어떤 이점이 있나요?
  - finally 블록을 통해 작업 성공, 실패, 취소 상황 모두에서 다운로드된 임시 디렉터리를 반드시 삭제하도록 보장함을 설명한다. → 레포지토리 클론 후 사용된 임시 디렉터리의 수명 주기는 어떻게 관리되나요?
- 꼬리질문:
  - 예외가 발생했을 때 failed 상태 보고 시 마지막 진행률을 함께 전달하도록 설계한 이유는 무엇인가요? (-)
  - requester 정보가 없을 때 contribution_lookup의 처리 결과는 전체 파이프라인 동작에 어떤 영향을 주나요? (worker/src/gitinsight_worker/pipeline.py::contribution_lookup)

## 안전한 Git 복제와 예외 처리 (`worker/src/gitinsight_worker/git.py::git_cloner`, 난이도 3, 기여 verified)

- 요약: git_cloner는 입력된 URL과 커밋 해시 유효성을 검증한 뒤 대상 레포지토리를 복제하는 클로너 함수를 생성합니다. 외부 프로세스로 git clone과 checkout을 순차적으로 실행하며 지정된 프로토콜과 환경 변수를 통제합니다. 실행 중 제한 시간 초과나 비정상 종료가 발생하면 CloneError 예외로 변환하여 처리합니다.
- 질문: git 실행 시 실패나 타임아웃 상황을 어떻게 처리하도록 구현하셨나요?
- 취지: 외부 프로세스를 실행할 때 발생할 수 있는 오류나 지연 상황을 어떻게 감지하고 예외로 다루었는지 묻는 것입니다.
- 힌트: clone 내부의 steps 반복문과 subprocess.run을 감싼 try-except 블록을 살펴보세요.
- 관련 심볼: worker/src/gitinsight_worker/git.py::CloneError, worker/src/gitinsight_worker/git.py::_auth_env
- 기대 포인트:
  - subprocess.run에 timeout을 설정해 clone과 checkout 단계에서 일정 시간을 초과하면 TimeoutExpired를 잡고 CloneError로 변환한다. → 외부 git 명령이 오랜 시간 응답하지 않을 때 어떻게 대처하도록 하셨나요?
  - check=True 설정을 통해 git 명령의 비정상 종료 시 CalledProcessError가 발생하며 이를 CloneError로 감싸서 던진다. → 명령어 실행 중 종료 코드가 0이 아닌 경우 어떤 방식으로 호출자에게 알리나요?
  - from None 구문을 사용하여 하위 subprocess 예외의 역추적 정보를 감추고 깔끔한 CloneError를 발생시킨다. → 예외를 다시 발생시킬 때 from None을 붙여준 특별한 의도가 있나요?
  - 인증 토큰 등 민감 정보가 환경 변수나 에러 추적 정보에 노출되지 않도록 안전하게 분리하여 주입한다. → 복제 과정에서 인증 정보가 외부나 에러에 노출되지 않게 어떤 점을 신경 쓰셨나요?
- 꼬리질문:
  - 복제와 체크아웃 두 단계로 명령을 나누어 실행하게 된 이유가 무엇인가요? (-)
  - CloneError로 예외를 추상화했을 때 상위 워커 로직에는 어떤 이점이 있나요? (worker/src/gitinsight_worker/git.py::CloneError)
  - _auth_env를 clone 단계의 환경 변수에만 전달하는 이유는 무엇인가요? (worker/src/gitinsight_worker/git.py::_auth_env)
