# 2단계 분석 평가 (2026-11-17)

- 레포: `tttae210-sys/gitinsight_agent` @ `6a3111ce7e36918a4332d63e25d59e471961879f`
- 모델: `gemini/gemini-3.8-flash (reasoning_effort=low)`
- 주제 수: 6
- 기여 라벨: verified 6개
- LLM 호출: 6회 (검증 실패 재요청 포함), 이 중 스키마 재시도 0회
- 비용: $0.0269
- 호출당 최대 지연: 8939 ms

## 이번 변경 (#58, 레포 안 참조 수)

1단계가 심볼 이름의 레포 안 참조 수를 세고, 2단계가 이를 서버 사실로 프롬프트에 넣은 뒤 다시 돌렸습니다.
요청자, 커밋, 모델은 [stage2-2026-11-17.md](stage2-2026-11-17.md)와 같고, 고른 주제 6개도 같습니다.

- 데모 레포에서 참조 0인 심볼(데코레이터·특수 메서드 제외 63개 중 11개): `_legacy_*` 6개, `StreamEvent`,
  `VectorService.has_documents`, `VectorService.index_documents`, `vector_store.search_documents`, `vector_store.seed_if_empty`.
  모두 grep으로도 부르는 곳이 없었습니다
- `VectorService`: `index_documents`를 부르는 곳을 찾지 못했다는 기대 포인트 1개와 꼬리질문 1개가 생겼습니다 (PR #54 리뷰에서 D가 짚은 문제)
- `search_documents`: 함수 자신과 관련 심볼 `seed_if_empty` 모두 부르는 곳이 없다는 기대 포인트와 꼬리질문이 생겼습니다
- `extract_active_db_resources`: 연결 문제를 다루지 않습니다. 이 함수는 `build_github_repo`가 부르므로 참조 수로는 잡히지 않습니다.
  실제 문제는 `VectorService`에 없는 속성 이름(`_client` 등)을 추측해서 찾아 늘 `(None, None)`이 되는 것이라,
  다른 파일의 정의를 같이 보여 줘야 짚을 수 있습니다. 후속 이슈로 남깁니다
- 표현: 대부분 "부르는 곳을 찾지 못했다"로 썼지만, 기대 포인트 하나는 "참조가 0곳으로 확인되는데"라고 단정에 가깝게 썼습니다

사람이 확인할 것: 기대 포인트가 코드에 실제로 있는가, 질문이 존댓말 한 문장이고 자연스러운가,
힌트가 답을 알려 주지 않는가, 관련 심볼이 꼬리질문과 맞는가.

## 깃허브 레포 빌드 흐름 (`app/agents/builder.py::build_github_repo`, 난이도 1, 기여 verified)

- 요약: GitHub URL에서 최신 커밋 해시를 조회하고 레포지토리와 커밋 기반의 컬렉션 이름을 생성합니다. 기존 ChromaDB에 해당 컬렉션이 이미 존재하면 캐시된 코드 청크와 기술 스택을 복원하고, 없으면 트리를 순회하여 소스코드를 다운로드한 뒤 DB에 저장합니다. 최종적으로 추출된 기술 스택과 코드 청크, 커밋 해시를 상태 딕셔너리로 반환합니다.
- 질문: build_github_repo 함수가 깃허브 코드를 수집하고 처리하는 전체 순서를 설명해 주세요.
- 취지: 이 함수가 깃허브 저장소 정보를 받아서 최종 결과를 반환하기까지 어떤 순서로 작업을 진행하는지 묻는 질문입니다.
- 힌트: build_github_repo 함수 내부에서 API 호출과 컬렉션 캐시 확인, 신규 저장 분기를 차례대로 살펴보세요.
- 관련 심볼: app/agents/builder.py::extract_active_db_resources
- 기대 포인트:
  - repo_url을 정규식으로 파싱하여 owner와 repo를 추출하고 최신 커밋 해시를 가져온 뒤 고유 컬렉션 이름을 만듭니다. → 저장소 식별과 커밋 해시 조회 이후 컬렉션 이름은 어떻게 구성되나요?
  - ChromaDB에 동일 컬렉션이 이미 있으면 캐시 히트로 판단하여 기존 데이터를 읽어 tech_stack과 extracted_chunks를 복원합니다. → 이미 처리된 커밋에 대해 캐시가 존재할 때는 어떤 과정을 거치나요?
  - 캐시가 없으면 깃허브 트리 API로 파일 목록을 조회하고 허용된 확장자의 코드를 최대 3개까지 수집하여 ChromaDB에 저장합니다. → 캐시가 없을 때 소스코드 파일은 어떤 기준으로 필터링되어 수집되나요?
- 꼬리질문:
  - extract_active_db_resources 함수에서 클라이언트를 가져오지 못했을 때 어떻게 동작하나요? (app/agents/builder.py::extract_active_db_resources)

## ChromaDB 벡터 인덱싱과 검색 설계 (`app/service/vector_service.py::VectorService`, 난이도 2, 기여 verified)

- 요약: VectorService 클래스는 ChromaDB 클라이언트 및 임베딩 함수를 초기화하여 단일 컬렉션을 관리합니다. 코드 청크 목록에 파일 경로를 포함한 텍스트와 메타데이터를 구성해 벡터 DB에 인덱싱하며, 조건 필터 기반의 존재 여부 확인 및 유사도 검색 기능을 제공합니다.
- 질문: 문서를 벡터화할 때 파일 경로와 코드를 조합하신 이유가 무엇인가요?
- 취지: 임베딩 대상 텍스트를 구성할 때 특정 포맷으로 파일 경로와 코드를 함께 묶도록 설계한 배경과 장단점을 여쭙는 질문입니다.
- 힌트: VectorService의 index_documents 메서드 내부에서 document_text 변수를 구성하는 로직을 살펴보세요.
- 관련 심볼: app/service/vector_service.py::VectorService.index_documents, app/service/vector_service.py::VectorService.search
- 기대 포인트:
  - 코드 내용뿐만 아니라 파일 경로라는 맥락 정보를 텍스트에 포함시켜 임베딩 모델이 모듈의 성격이나 위치를 함께 반영하도록 유도한 점 → 텍스트 임베딩 시 파일 경로를 포함했을 때 검색 품질 측면에서 어떤 차이가 생기나요?
  - desc 같은 요약 설명은 텍스트 임베딩 대상에서 제외하고 메타데이터로만 저장하도록 분리한 구조적 의도 → desc 필드는 왜 문서 본문에 합치지 않고 메타데이터로만 관리하셨나요?
  - 레포 안에서 index_documents의 직접 호출 참조가 0곳으로 확인되는데, 이 적재 로직이 시스템의 어떤 파이프라인에서 호출되도록 설계되었는지 설명 → index_documents 메서드를 부르는 곳을 찾지 못했는데, 실제로 어디서 이 작업이 트리거되나요?
- 꼬리질문:
  - index_documents 메서드는 레포 내 직접 참조를 찾지 못했는데, 실제 런타임에서 어떤 흐름으로 호출되나요? (app/service/vector_service.py::VectorService.index_documents)
  - search 메서드에서 query_texts 기반 질의 시 where 필터 예외 처리를 단순 pass 형태로 둔 이유는 무엇인가요? (app/service/vector_service.py::VectorService.search)

## SSE 스트리밍 예외 처리 (`app/api.py::chat_stream`, 난이도 3, 기여 verified)

- 요약: chat_stream 함수는 사용자의 답변과 요청 정보를 받아 LangGraph 워크플로우를 비동기로 실행합니다. 실행 중 노드 상태 변화와 최종 채점 결과를 Server-Sent Events(SSE) 포맷으로 실시간 스트리밍합니다. 연산 도중 예외가 발생하면 에러 이벤트를 클라이언트에 전달하도록 구성되어 있습니다.
- 질문: 스트리밍 중 예외가 발생했을 때 클라이언트와 서버는 어떻게 동작하나요?
- 취지: 예외가 터졌을 때 스트림 연결 및 클라이언트에 대한 처리 방식과 서버 내부 상태에 어떤 영향이 있는지 묻는 질문입니다.
- 힌트: chat_stream 함수 내부의 event_generator 구현과 try-except 처리부를 확인해 보세요.
- 관련 심볼: app/api.py::chat_sync, app/api.py::chat_reset
- 기대 포인트:
  - event_generator 내부 try-except 블록에서 에러 메시지를 SSE error 이벤트 포맷으로 클라이언트에 yield한 뒤 스트림이 종료됩니다. → 워크플로우 실행 중 예외가 발생하면 클라이언트는 어떤 형태의 데이터를 받게 되나요?
  - 예외 발생 시 logging.error로 기록하지만, 그래프 상태 롤백이나 재시도 로직 없이 단일 에러 이벤트 전송으로 연결이 끝납니다. → 에러가 발생한 이후 LangGraph 워크플로우 상태는 별도로 복구되거나 처리되나요?
  - 클라이언트가 응답을 수신한 후 연결이 끊어지면 SSE 재연결 동작이나 후속 처리가 클라이언트 구현에 의존하게 됩니다. → 스트림이 에러 이벤트를 보내고 종료된 후 클라이언트 단에서는 어떤 상황이 발생할 수 있을까요?
- 꼬리질문:
  - 동기식 처리를 담당하는 chat_sync와 비교했을 때 예외 반환 방식에 어떤 차이가 있나요? (app/api.py::chat_sync)
  - 네트워크 단절로 스트림이 중단되었을 때 graph 상태를 복구하거나 초기화하는 chat_reset과의 연계 방안이 있나요? (app/api.py::chat_reset)

## DB 자원 동적 추출 과정 (`app/agents/builder.py::extract_active_db_resources`, 난이도 1, 기여 verified)

- 요약: 기존에 동작 중인 VectorService에서 Chroma 클라이언트와 임베딩 함수를 찾아 튜플로 반환합니다. 클라이언트는 특정 속성 이름이나 객체 전체 속성을 검사하여 대상 클래스 타입인지를 확인해 추출합니다. 임베딩 함수는 메서드 존재 여부를 확인한 후 LangChainEmbeddingAdapter로 감싸서 반환합니다.
- 질문: extract_active_db_resources 함수는 필요한 자원을 어떤 순서로 추출하나요?
- 취지: 해당 함수에서 Chroma 클라이언트와 임베딩 모델을 찾아 반환하는 구체적인 탐색 순서와 방식을 묻는 질문입니다.
- 힌트: 함수 내부에서 get_vector_service 호출 후 진행되는 for 루프들과 반환값을 살펴보세요.
- 관련 심볼: app/agents/builder.py::LangChainEmbeddingAdapter, app/agents/builder.py::build_github_repo
- 기대 포인트:
  - get_vector_service 함수 유무를 확인하고 서비스를 가져온 뒤 Chroma 클라이언트와 임베딩 함수를 차례로 탐색합니다. → 함수가 시작될 때 VectorService 객체는 어떻게 가져오나요?
  - Chroma 클라이언트는 사전에 정의된 속성 이름을 먼저 살피고, 없으면 객체의 전체 속성을 순회하며 클래스 이름을 대조해 찾습니다. → Chroma 클라이언트를 찾지 못했을 때 어떤 방식으로 다시 탐색하나요?
  - 임베딩 모델은 후보 속성 중 embed_query와 embed_documents 메서드를 가진 객체를 찾아 어댑터 클래스로 감싸서 반환합니다. → 추출한 임베딩 모델은 어떤 처리를 거쳐 반환되나요?
- 꼬리질문:
  - 임베딩 모델을 LangChainEmbeddingAdapter로 한 번 더 감싸서 반환하는 이유는 무엇인가요? (app/agents/builder.py::LangChainEmbeddingAdapter)
  - build_github_repo 함수에서는 이렇게 추출된 DB 자원을 구체적으로 어떻게 활용하나요? (app/agents/builder.py::build_github_repo)

## PDF 텍스트 추출 설계 (`frontend/ui.py::extract_text_from_pdf`, 난이도 2, 기여 verified)

- 요약: 업로드된 PDF 파일의 바이트를 읽어 pypdf 라이브러리로 페이지별 텍스트를 순회하며 추출합니다. 라이브러리 부재 시 안내 문구를 반환하고, 파싱 중 예외가 발생하면 에러 메시지를 반환하도록 처리되어 있습니다.
- 질문: PDF 텍스트 추출 중 오류가 발생할 때 예외를 던지지 않고 문자열을 반환하도록 설계한 이유는 무엇인가요?
- 취지: 예외를 상위로 전파하지 않고 에러 문구 문자열을 직접 반환하도록 구현한 이유와 그 설계상 장단점을 묻는 질문입니다.
- 힌트: extract_text_from_pdf 함수에서 PYPDF_AVAILABLE 분기와 try-except 구문의 반환 형태를 확인해 보세요.
- 관련 심볼: 없음
- 기대 포인트:
  - try-except 블록에서 Exception 발생 시 예외를 전파하지 않고 에러 메시지 문자열을 반환하여 UI 흐름이 중단되지 않도록 처리한 점 → 파싱 중 예외가 발생했을 때 상위 호출부로 에러를 전파하지 않는 방식의 장단점은 무엇인가요?
  - PYPDF_AVAILABLE 플래그를 두어 라이브러리 미설치 시에도 런타임 오류 없이 안내 메시지를 반환하도록 한 점 → PYPDF_AVAILABLE 변수를 통해 의존성 유무를 체크하도록 분기 처리한 목적은 무엇인가요?
  - 에러 문자열을 일반 반환값과 동일한 str 타입으로 전달할 때 호출부가 성공 여부를 구분하기 어려워질 수 있다는 한계 → 성공 텍스트와 에러 안내 문구가 모두 문자열로 반환될 때 호출부에서 발생할 수 있는 문제는 무엇인가요?
- 꼬리질문:
  - 대용량 PDF 파일이 업로드되어 getvalue로 전체를 메모리에 올릴 때 발생할 수 있는 부하와 개선 방안은 무엇인가요? (-)
  - 파싱 실패 문자열이 그대로 반환될 경우 UI 후속 단계에서 어떻게 정상 텍스트와 구분하여 처리할 수 있을까요? (-)

## 문서 검색 필터와 예외 처리 (`app/vector_store.py::search_documents`, 난이도 3, 기여 verified)

- 요약: search_documents 함수는 repo_url과 commit_hash 인자를 기반으로 벡터 저장소 검색을 위한 필터 조건을 구성합니다. 구성된 필터와 검색 인자를 넘겨 get_vector_service().search를 호출하여 검색 결과를 반환합니다. 이전 코드베이스와의 호환성을 유지하기 위한 래퍼 함수 역할을 수행합니다.
- 질문: 인자 조합에 따른 필터 구성과 서비스 장애 시의 예외 처리 방식에 대해 설명해 주시겠습니까?
- 취지: 입력값 검증이나 필터링 조건 설정, 그리고 외부 서비스 호출 실패 시 어떤 대비책이 있는지 묻는 질문입니다.
- 힌트: search_documents 함수의 if-elif 분기문과 get_vector_service().search 호출 부분을 살펴보세요.
- 관련 심볼: app/vector_store.py::seed_if_empty
- 기대 포인트:
  - repo_url만 있거나 둘 다 있는 경우만 처리되고 commit_hash만 전달된 경우는 필터에 반영되지 않는 조건부 로직 구조 → commit_hash만 전달되었을 때 필터 조건은 어떻게 동작하나요?
  - get_vector_service().search 호출 과정에서 발생할 수 있는 네트워크 오류나 벡터 스토어 장애에 대한 예외 처리 부재 → 벡터 서비스 호출 중 오류가 발생하면 어떻게 처리되나요?
  - 현재 레포지토리 내에서 search_documents를 직접 호출하는 참조를 찾지 못했으므로 실제 호출 위치나 연동 계획 확인 필요 → 레포 내에서 이 함수를 부르는 곳을 찾지 못했는데 실제로 어디와 연결되나요?
- 꼬리질문:
  - seed_if_empty 함수도 레포 내 호출처를 찾지 못했는데, 초기 데이터 적재와 검색의 연동 흐름은 어떻게 되나요? (app/vector_store.py::seed_if_empty)

## 손으로 만든 샘플과 비교

- 심볼 수: 워커 75, 샘플 75
- 같은 심볼을 고른 주제: 2개 (워커 6개, 샘플 6개)
  - `app/agents/builder.py::build_github_repo` 줄 86-252
  - `app/service/vector_service.py::VectorService` 줄 21-120
- 워커만 고른 심볼: `app/agents/builder.py::extract_active_db_resources`, `app/api.py::chat_stream`, `app/vector_store.py::search_documents`, `frontend/ui.py::extract_text_from_pdf`
- 샘플만 고른 심볼: `app/agents/classifier.py::classify_user_intent`, `app/agents/question_extractor.py::extract_question_pool`, `app/graph.py::create_graph`, `app/graph.py::next_question_node`
