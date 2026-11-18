# 2단계 분석 평가 (2026-11-18)

- 레포: `tttae210-sys/gitinsight_agent` @ `6a3111ce7e36918a4332d63e25d59e471961879f`
- 모델: `gemini/gemini-3.8-flash (reasoning_effort=low)`
- 주제 수: 6
- 기여 라벨: verified 6개
- LLM 호출: 6회 (검증 실패 재요청 포함), 이 중 스키마 재시도 0회
- 비용: $0.0294
- 호출당 최대 지연: 8175 ms

## 이번 변경 (#66, 다른 파일 정의 요약)

주제 코드가 쓰는 다른 파일의 최상위 함수·클래스(반환 타입 클래스까지 한 단계)를 시그니처, 클래스 속성 이름, 메서드 시그니처로 요약해
`untrusted_block("external_definitions")`에 넣고 다시 돌렸습니다. 요청자, 커밋, 모델은 [stage2-2026-11-17-references.md](stage2-2026-11-17-references.md)와 같고, 고른 주제 6개도 같습니다.

- 데모 레포에서 요약이 붙은 주제: `extract_active_db_resources`와 `search_documents`(`get_vector_service` → `VectorService`, 약 430자), `build_github_repo`(`InterviewState`), `chat_stream`(`ChatRequest`)
- `extract_active_db_resources`: 처음으로 연결 결함을 다룹니다.
  "VectorService에서 _client나 embeddings 같은 속성을 찾지 못했는데, 이 함수가 실제로 정상 동작하나요?" (기대 포인트 p4의 probe).
  `VectorService`에는 `_collection` 속성만 있어서 이 함수는 늘 `(None, None)`을 돌려줍니다 (PR #54 리뷰에서 D가 짚은 문제)
- 1회차(이 기록 전)에는 같은 결함을 짚었지만 질문이 "VectorService **정의 요약**에 없는 속성들을…"처럼 지원자가 보지 못한 요약을 가리켰고,
  `search_documents` 요점이 "예외가 호출자에게 그대로 전파"라고 다른 메서드 안의 동작을 단정했습니다 (실제로는 `VectorService.search`가 검색 예외를 잡음).
  시스템 프롬프트에 "요약이라는 말 대신 코드 이름으로 말한다", "보이지 않는 본문의 동작은 단정하지 않는다"를 더하고 다시 돌린 것이 이 기록입니다 (1회 $0.0278, 2회 $0.0294)
- 남은 점: `search_documents` p1은 "이 함수에서 예외를 처리하는 로직이 없다"로, 함수 자체로는 맞지만 `search` 안에서 잡는다는 답을 놓칠 수 있습니다
- 데모 스냅샷에는 `extract-active-db-resources` 주제만 이 결과로 바꿨습니다. 나머지 주제는 #67에서 리뷰에 따라 손으로 고친 문장을 그대로 둡니다

사람이 확인할 것: 기대 포인트가 코드에 실제로 있는가, 질문이 존댓말 한 문장이고 자연스러운가,
힌트가 답을 알려 주지 않는가, 관련 심볼이 꼬리질문과 맞는가.

## 깃허브 레포 빌드 흐름 (`app/agents/builder.py::build_github_repo`, 난이도 1, 기여 verified)

- 요약: build_github_repo 함수는 전달받은 레포지토리 URL에서 소유자와 이름을 파악하고 최신 커밋 해시를 조회합니다. 동일 커밋으로 캐시된 ChromaDB 컬렉션이 있으면 기존 청크를 불러오고, 없으면 파일 트리를 조회해 코드를 가져와 새로 임베딩하여 컬렉션에 저장합니다. 추출된 기술 스택과 코드 청크, 커밋 해시를 딕셔너리로 반환합니다.
- 질문: build_github_repo 함수가 깃허브 저장소를 분석하고 처리하는 전체 순서를 설명해 주시겠습니까?
- 취지: 이 함수가 깃허브 저장소 정보를 받아 데이터베이스 캐시를 확인하고 코드를 수집하여 반환하기까지의 기본 진행 과정을 묻고 있습니다.
- 힌트: 함수 시작부의 정규표현식 파싱부터 캐시 적중 분기와 캐시 미스 분기의 진행 과정을 살펴보세요.
- 관련 심볼: app/agents/builder.py::extract_active_db_resources
- 기대 포인트:
  - repo_url을 파싱하여 소유자와 레포명을 추출하고 깃허브 API로 최신 커밋 해시를 조회한다. → 저장소 주소로부터 커밋 해시를 가져올 때 어떤 과정을 거치나요?
  - 컬렉션 이름을 구성한 뒤 ChromaDB에 이미 존재하는지 확인하여 캐시 적중 여부를 판단한다. → 기존에 분석된 커밋 데이터가 있는지 확인하는 기준은 무엇인가요?
  - 캐시가 없으면 깃허브 트리 API로 파일들을 탐색해 확장자 기준 기술 스택을 분류하고 일부 파일 코드를 ChromaDB에 저장한다. → 캐시가 존재하지 않는 신규 커밋일 때는 파일들을 어떻게 처리하나요?
  - 분석된 tech_stack, extracted_chunks, repo_commit_hash 결과를 딕셔너리로 반환한다. → 함수 실행이 완료되면 최종적으로 어떤 정보들이 반환되나요?
- 꼬리질문:
  - extract_active_db_resources 함수에서 클라이언트를 얻지 못했을 때는 어떤 방식으로 동작하나요? (app/agents/builder.py::extract_active_db_resources)
  - 캐시 미스 상황에서 코드 파일을 최대 3개까지만 수집하도록 제한을 둔 이유는 무엇인가요? (-)

## 벡터 검색 인덱싱 및 필터링 설계 (`app/service/vector_service.py::VectorService`, 난이도 2, 기여 verified)

- 요약: ChromaDB와 임베딩 함수를 초기화하여 코드 청크를 인덱싱하고 유사도를 검색하는 서비스 클래스입니다. 청크 메타데이터와 파일 경로를 묶어 문서를 저장하고, 조건부 where 필터를 활용해 컬렉션 내 문서를 확인하거나 검색합니다.
- 질문: 문서를 임베딩할 때 파일 경로와 코드를 합쳐 구성하신 의도는 무엇인가요?
- 취지: 임베딩 대상 텍스트의 형식을 이렇게 구성한 이유와 장단점을 묻는 질문입니다.
- 힌트: VectorService 클래스의 index_documents 메서드에서 document_text 변수를 생성하는 부분을 확인해 보세요.
- 관련 심볼: app/service/vector_service.py::VectorService.index_documents, app/service/vector_service.py::VectorService.search, app/service/vector_service.py::VectorService.has_documents
- 기대 포인트:
  - 단순 코드뿐만 아니라 파일 경로 정보를 함께 제공하여 파일의 역할이나 맥락 같은 의미 정보를 임베딩 벡터에 반영하려 했음을 설명한다. → 코드만 단독으로 임베딩하는 것과 비교했을 때 경로를 포함하면 어떤 장점이 있나요?
  - 코드 청크 단위로 분할되었을 때 소실될 수 있는 위치 정보나 모듈 간 맥락을 보완하는 설계 의도를 다룬다. → 검색 정확도나 문맥 파악 측면에서 이런 텍스트 구성이 주는 효과는 무엇인가요?
  - index_documents 메서드를 레포 안에서 호출하는 곳을 찾지 못했는데, 실제로 어디서 데이터를 인덱싱하도록 계획했는지 설명한다. → 레포 안에서 index_documents를 부르는 곳을 찾지 못했는데 실제로 어떻게 쓰이나요?
- 꼬리질문:
  - search 메서드에서 query_texts 기반 질의 시 발생할 수 있는 ChromaDB 예외를 어떻게 처리하셨나요? (app/service/vector_service.py::VectorService.search)
  - has_documents 메서드를 호출하는 곳을 레포 안에서 찾지 못했는데, 어떤 흐름에서 사용되나요? (app/service/vector_service.py::VectorService.has_documents)

## SSE 스트리밍 예외 처리 (`app/api.py::chat_stream`, 난이도 3, 기여 verified)

- 요약: chat_stream 함수는 사용자의 답변 입력을 받아 LangGraph 워크플로우를 스트리밍 방식으로 실행합니다. 각 노드의 실행 상태를 실시간 SSE 이벤트로 전송하고, 실행 완료 후 최종 채점 결과를 전송합니다. 도중 예외가 발생하면 에러 로그를 남기고 클라이언트에 에러 이벤트를 반환합니다.
- 질문: SSE 스트리밍 중 예외가 발생했을 때 클라이언트는 이를 어떻게 전달받나요?
- 취지: 스트리밍 도중 오류가 발생했을 때 예외가 어떻게 처리되고 클라이언트에 어떤 형태로 응답이 가는지 묻는 질문입니다.
- 힌트: event_generator 내부의 try-except 구문과 반환되는 SSE 데이터 구조를 확인해 보세요.
- 관련 심볼: app/api.py::chat_sync
- 기대 포인트:
  - event_generator 내부 전체를 try-except 블록으로 감싸 예외 발생 시 logging.error로 기록함을 설명한다. → 스트리밍 중 예외가 발생하면 서버 측에서는 어떻게 기록을 남기나요?
  - 예외 발생 시 HTTP 오류 코드가 아닌 error 이벤트 형태의 SSE 메시지로 yield 처리됨을 말한다. → 예외가 났을 때 클라이언트로 전달되는 이벤트 메시지의 구조는 어떻게 되나요?
  - StreamingResponse가 이미 200 상태 코드로 시작된 후 발생하는 예외를 SSE 규격 내에서 완충하려는 구조임을 설명한다. → HTTP 상태 코드 대신 SSE 데이터로 에러를 전송하도록 설계한 이유는 무엇인가요?
- 꼬리질문:
  - 동기식으로 결과를 한 번에 돌려주는 chat_sync와 비교했을 때 예외 처리 방식에 어떤 차이가 있나요? (app/api.py::chat_sync)

## DB 자원 동적 추출 과정 (`app/agents/builder.py::extract_active_db_resources`, 난이도 1, 기여 verified)

- 요약: 기존에 실행 중인 VectorService에서 ChromaDB 클라이언트와 임베딩 함수를 찾아 튜플로 반환합니다. 클라이언트는 특정 속성 이름이나 인스턴스 전체 속성을 탐색하여 클래스 타입으로 판별합니다. 임베딩 모델의 경우 embed_query와 embed_documents 메서드를 갖춘 속성을 찾아 LangChainEmbeddingAdapter로 감싸서 반환합니다.
- 질문: extract_active_db_resources 함수는 어떤 순서로 동작하나요?
- 취지: 이 함수가 자원을 가져오기 위해 어떤 과정을 거치는지 단계별 흐름을 묻는 질문입니다.
- 힌트: VectorService 인스턴스를 가져온 뒤 ChromaDB 클라이언트와 임베딩 모델을 각각 찾는 과정을 살펴보세요.
- 관련 심볼: app/agents/builder.py::LangChainEmbeddingAdapter, app/agents/builder.py::build_github_repo
- 기대 포인트:
  - get_vector_service 함수 유무를 확인한 뒤 이를 호출하여 VectorService 인스턴스를 얻는 것으로 시작함을 설명한다. → VectorService 인스턴스를 가져오기 위해 처음에 어떤 검사와 호출을 수행하나요?
  - 지정된 속성명 리스트와 dir() 전체 속성을 순회하며 ChromaDB 클라이언트 클래스 타입을 찾아낸다. → ChromaDB 클라이언트는 어떤 방식으로 VectorService 내부에서 탐색하나요?
  - 임베딩 모델 속성을 찾아 필요한 메서드 유무를 확인한 후 LangChainEmbeddingAdapter로 감싸 반환한다. → 임베딩 모델을 찾아낸 뒤에는 어떤 처리를 거쳐 반환하나요?
  - VectorService 클래스에서 _client 등의 속성을 찾지 못했는데, 실제로 이 속성들이 존재하는지 인지하고 동작을 설명한다. → VectorService에서 _client나 embeddings 같은 속성을 찾지 못했는데, 이 함수가 실제로 정상 동작하나요?
- 꼬리질문:
  - 임베딩 모델을 LangChainEmbeddingAdapter로 감싸서 반환하는 이유는 무엇인가요? (app/agents/builder.py::LangChainEmbeddingAdapter)
  - 클라이언트 검색 시 특정 속성명을 먼저 확인하고 dir()로 다시 탐색하는 이유는 무엇인가요? (-)

## PDF 텍스트 추출 설계 (`frontend/ui.py::extract_text_from_pdf`, 난이도 2, 기여 verified)

- 요약: 업로드된 PDF 파일의 바이트 스트림을 메모리 상에서 읽어 pypdf 라이브러리로 페이지별 텍스트를 추출합니다. 라이브러리 미설치나 파싱 중 예외가 발생하면 오류 안내 문자열을 반환합니다.
- 질문: PDF 파싱 실패나 라이브러리 부재 시 안내 문자열을 직접 반환하도록 설계한 이유는 무엇인가요?
- 취지: 예외를 발생시키거나 별도 에러 타입을 쓰지 않고 오류 문구를 일반 문자열로 반환하도록 설계한 이유를 묻는 질문입니다.
- 힌트: extract_text_from_pdf의 라이브러리 확인 조건문과 try-except 블록의 반환값을 확인해 보세요.
- 관련 심볼: 없음
- 기대 포인트:
  - 라이브러리가 없거나 예외가 발생해도 예외를 발생시키지 않고 사용자 친화적인 안내 문자열을 반환하여 UI 렌더링 중단을 방지함을 설명한다. → 예외를 상위 호출자로 전달하지 않고 특정 문자열로 감싸서 반환하면 UI 관점에서 어떤 장점이 있나요?
  - 호출 측에서 반환값이 정상 텍스트인지 오류 메시지인지 구분하기 모호해질 수 있다는 설계상 트레이드오프를 인지하고 설명한다. → 반환 타입이 str로 통일되어 있을 때 상위 로직에서 실제 오류 상황을 식별하는 데 어떤 한계가 있을까요?
  - io.BytesIO와 getvalue()를 사용해 업로드된 파일을 디스크에 저장하지 않고 메모리 내에서 직접 처리한 이유를 설명한다. → 파일을 디스크에 저장하지 않고 io.BytesIO로 메모리에서 읽도록 처리한 이유가 무엇인가요?
- 꼬리질문:
  - 파싱에 실패했을 때 None을 반환하거나 예외를 던지는 방식과 비교해 현재 방식의 장단점은 무엇인가요? (-)
  - 페이지별 텍스트를 단순 연결하고 있는데, 구조화된 서식이나 이미지 기반 PDF는 어떻게 처리할 계획인가요? (-)

## search_documents 예외 및 필터링 동작 (`app/vector_store.py::search_documents`, 난이도 3, 기여 verified)

- 요약: search_documents는 repo_url과 commit_hash 인자를 기반으로 ChromaDB용 검색 필터를 구성합니다. 구성된 필터와 쿼리를 바탕으로 VectorService 인스턴스를 가져와 search 메서드를 호출하고 결과를 반환합니다.
- 질문: 검색 서비스 호출 시 발생할 수 있는 장애나 예외 상황에 어떻게 대응할 수 있나요?
- 취지: 외부 벡터 서비스 연동 중 오류나 부적절한 인자가 들어왔을 때 안정적으로 처리하는 방안을 묻는 질문입니다.
- 힌트: search_documents의 조건문 처리 방식과 get_vector_service() 호출부를 살펴보세요.
- 관련 심볼: app/vector_store.py::seed_if_empty
- 기대 포인트:
  - get_vector_service() 조회나 search 메서드 호출 과정에서 발생 가능한 연결 오류나 예외를 처리하는 로직이 없음을 인지하고 대안을 제시한다. → VectorService와의 통신 실패나 예외가 발생하면 이 함수에서 어떻게 처리되나요?
  - repo_url 없이 commit_hash만 전달될 경우 필터링 조건에서 누락되는 점을 파악하고 올바른 필터 정책을 설명한다. → commit_hash만 들어오고 repo_url이 None일 때는 필터가 어떻게 동작하나요?
  - 레포 안에서 이 함수를 부르는 곳을 찾지 못했다는 점을 인지하고, 호환성 래퍼로서 향후 사용 계획이나 정리 방안을 설명한다. → 레포 안에서 이 함수를 부르는 곳을 찾지 못했는데, 지금 실제로 쓰이고 있나요?
- 꼬리질문:
  - seed_if_empty 또한 레포 안에서 호출을 찾지 못했는데 두 함수 모두 이전 인터페이스 호환을 위해 남겨둔 것인가요? (app/vector_store.py::seed_if_empty)
  - 검색 결과가 없거나 n_results에 잘못된 값이 입력될 때의 검증 방안은 무엇인가요? (-)

## 손으로 만든 샘플과 비교

- 심볼 수: 워커 75, 샘플 75
- 같은 심볼을 고른 주제: 2개 (워커 6개, 샘플 6개)
  - `app/agents/builder.py::build_github_repo` 줄 86-252
  - `app/service/vector_service.py::VectorService` 줄 21-120
- 워커만 고른 심볼: `app/agents/builder.py::extract_active_db_resources`, `app/api.py::chat_stream`, `app/vector_store.py::search_documents`, `frontend/ui.py::extract_text_from_pdf`
- 샘플만 고른 심볼: `app/agents/classifier.py::classify_user_intent`, `app/agents/question_extractor.py::extract_question_pool`, `app/graph.py::create_graph`, `app/graph.py::next_question_node`
