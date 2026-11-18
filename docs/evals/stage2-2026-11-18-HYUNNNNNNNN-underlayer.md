# 2단계 분석 평가 (2026-11-18)

- 레포: `HYUNNNNNNNN/underlayer` @ `a0f0c277769b08c6d46f64b15a6b761a5249826b`
- 모델: `gemini/gemini-3.8-flash (reasoning_effort=low)`
- 주제 수: 6
- 기여 라벨: unverified 6개
- LLM 호출: 6회 (검증 실패 재요청 포함), 이 중 스키마 재시도 0회
- 비용: $0.0294
- 호출당 최대 지연: 7936 ms

사람이 확인할 것: 기대 포인트가 코드에 실제로 있는가, 질문이 존댓말 한 문장이고 자연스러운가,
힌트가 답을 알려 주지 않는가, 관련 심볼이 꼬리질문과 맞는가.

## MyProfile 컴포넌트 동작 (`app/components/MyProfile.tsx::MyProfile`, 난이도 1, 기여 unverified)

- 요약: 세션 정보를 바탕으로 사용자의 프로필 정보와 레이어 통계를 비동기로 조회해 화면에 표시합니다. 닉네임 수정 및 프로필 이미지 업로드를 API 요청으로 처리하고 상태를 갱신합니다. 레이어 개수에 따라 완성도 단계와 시각 효과를 동적으로 계산합니다.
- 질문: MyProfile에서 프로필 사진을 업로드할 때 어떤 순서로 동작하나요?
- 취지: 프로필 이미지를 선택했을 때 서버 전송과 화면 반영이 어떤 단계로 진행되는지 확인하려는 질문입니다.
- 힌트: handleImageUpload 함수 내부에서 파일을 읽고 요청을 보내는 흐름을 살펴보세요.
- 관련 심볼: app/components/MyProfile.tsx::MyProfile.handleImageUpload, app/components/MyProfile.tsx::MyProfile.handleSave, app/components/MyProfile.tsx::MyProfile.fetchMyStats
- 기대 포인트:
  - 선택된 파일 크기를 5MB 이하인지 검증한 뒤 FileReader로 Base64 데이터 URL을 읽어들인다. → 사용자가 선택한 파일에 대해 어떤 검증과 변환 과정을 거치나요?
  - 변환된 Base64 문자열을 /api/profile 경로에 PATCH 메서드로 전송한다. → 인코딩된 이미지 데이터를 어느 경로와 방식으로 서버에 전달하나요?
  - 서버 응답이 정상이면 displayImage 상태를 갱신하고 토스트 알림을 띄우며 라우터를 새로고침한다. → 서버 응답이 성공한 뒤 화면 상태와 알림은 어떻게 처리하나요?
- 꼬리질문:
  - handleSave에서는 닉네임을 변경할 때 어떤 절차로 요청을 보내나요? (app/components/MyProfile.tsx::MyProfile.handleSave)
  - fetchMyStats에서 전체 레이어 중 내 레이어는 어떻게 추려내나요? (app/components/MyProfile.tsx::MyProfile.fetchMyStats)

## 댓글 작성 시 닉네임 결정 설계 (`app/api/layers/[id]/comments/route.ts::POST`, 난이도 2, 기여 unverified)

- 요약: 특정 레이어에 새 댓글을 등록하는 API 핸들러입니다. 요청 본문의 내용 유효성을 검사한 뒤 이메일 기반으로 프로필을 조회해 닉네임을 결정하고 댓글을 생성합니다. 처리 도중 에러가 발생하면 500 응답을 반환합니다.
- 질문: 댓글 작성 시 닉네임을 결정하고 저장하도록 설계하신 이유가 무엇인가요?
- 취지: 요청 본문의 정보와 프로필 정보를 조합하여 최종 닉네임을 확정하고 저장하는 설계 의도를 묻는 것입니다.
- 힌트: POST 핸들러 내부에서 finalNickname 변수를 결정하는 조건문과 댓글 생성 부분을 확인해 보세요.
- 관련 심볼: app/api/layers/[id]/comments/route.ts::GET
- 기대 포인트:
  - 요청에 authorEmail이 있을 경우 userProfile 테이블을 조회하여 최신 닉네임이 있다면 덮어쓰도록 우선순위를 두었음을 설명한다. → 이메일이 제공되었을 때 프로필을 먼저 조회하도록 구성한 이유는 무엇인가요?
  - 요청 본문에 닉네임이 없거나 프로필 닉네임이 없는 경우 기본값으로 '익명의 도전자'를 사용하도록 폴백 방식을 설명한다. → 사용자 정보가 부족할 때 기본 닉네임을 지정하는 방식에 대해 설명해 주실 수 있나요?
  - 사용자 테이블과 댓글 테이블을 외래키로 직접 조인하는 대신 댓글 엔티티에 authorName과 authorEmail을 직접 저장하도록 설계한 배경을 설명한다. → 댓글을 유저 식별자와 외래키 관계로 연결하지 않고 직접 문자열 정보를 저장하도록 한 이유가 무엇인가요?
- 꼬리질문:
  - 동일한 라우트에 정의된 GET 함수와 비교했을 때, 댓글 조회 시 프로필 연동은 어떻게 처리되고 있나요? (app/api/layers/[id]/comments/route.ts::GET)
  - 클라이언트가 전달한 authorEmail의 신원 검증 없이 그대로 조회 및 저장에 사용될 때 발생할 수 있는 보안 문제는 어떻게 고려하셨나요? (-)

## 캔버스 비동기 작업 및 캡처 예외 처리 (`app/components/MyCanvas.tsx::MyCanvas`, 난이도 3, 기여 unverified)

- 요약: MyCanvas 컴포넌트는 사용자의 레이어 목록을 조회하여 시각적으로 보여주고 삭제, 공감, 이미지 내보내기 기능을 제공합니다. useEffect를 통해 로그인된 사용자의 레이어를 가져와 정렬 및 필터링을 수행합니다. toPng 라이브러리를 사용해 특정 DOM 영역을 캡처하여 다운로드할 수 있게 지원합니다.
- 질문: 네트워크 지연이나 캡처 실패 같은 비동기 작업 예외 상황에서 UI 상태는 어떻게 보장되나요?
- 취지: 비동기 요청이나 브라우저 캡처 중 오류가 발생했을 때 로딩 상태나 알림이 정상적으로 복구되는지 묻는 것입니다.
- 힌트: 컴포넌트 내의 여러 비동기 핸들러 함수들에서 try, catch, finally 블록이 상태 변수를 어떻게 갱신하는지 확인해 보세요.
- 관련 심볼: app/components/MyCanvas.tsx::MyCanvas.handleExportImage, app/components/MyCanvas.tsx::MyCanvas.handleDelete, app/components/MyCanvas.tsx::MyCanvas.fetchMyLayers, app/components/MyCanvas.tsx::MyCanvas.handleRespect
- 기대 포인트:
  - fetchMyLayers에서 API 호출 실패 시 try-catch 블록과 finally를 통해 isLoading 상태를 false로 확실하게 전환한다. → 데이터를 불러오는 도중 오류가 발생하면 사용자 화면의 로딩 인디케이터는 어떻게 처리되나요?
  - handleExportImage에서 toPng 실행 도중 오류가 나더라도 finally 블록을 통해 isExporting 상태를 false로 복구하고 알림을 닫는다. → 이미지 캡처 과정에서 예외가 발생할 때 내보내기 버튼의 비활성화 상태는 어떻게 해제되나요?
  - handleDelete에서 삭제 API 응답이 실패 상태코드일 때 명시적으로 Error를 던져 catch 블록에서 로딩 토스트를 닫고 에러 토스트를 표시한다. → 삭제 요청에 실패했을 때 로딩 토스트와 에러 표시는 어떤 흐름으로 제어되나요?
- 꼬리질문:
  - toPng가 오래 걸리거나 DOM을 찾지 못할 때 사용자에게 알림을 주는 방법은 어떻게 개선할 수 있을까요? (app/components/MyCanvas.tsx::MyCanvas.handleExportImage)
  - handleRespect에서 네트워크 요청 실패 시 사용자에게 실패 피드백을 주는 처리 방식에 대해 설명해 주실 수 있나요? (app/components/MyCanvas.tsx::MyCanvas.handleRespect)

## 프로필 등록 및 수정 로직 흐름 (`app/api/profile/route.ts::POST`, 난이도 1, 기여 unverified)

- 요약: 요청 본문에서 이메일과 닉네임을 추출하고 유효성을 검사합니다. UserProfile 테이블에 upsert를 수행한 뒤 Layer와 Comment 테이블의 작성자 이름을 업데이트합니다. 작업 중 오류가 발생하면 에러 응답을 반환합니다.
- 질문: POST 함수가 요청을 받아 프로필을 처리하는 순서를 설명해 주세요.
- 취지: 요청이 들어왔을 때 데이터 검증부터 데이터베이스 작업까지 어떤 단계로 진행되는지 묻는 질문입니다.
- 힌트: POST 함수의 본문에서 조건문 검사와 prisma 호출 순서를 살펴보세요.
- 관련 심볼: app/api/profile/route.ts::PATCH, app/api/profile/route.ts::GET
- 기대 포인트:
  - 요청 본문에서 email과 nickname을 가져와 값이 누락되었을 때 400 응답을 반환하는 유효성 검증을 거친다. → 필수 데이터가 전달되지 않았을 때는 어떤 방식으로 처리되나요?
  - email을 기준으로 UserProfile에 upsert를 수행해 프로필을 생성하거나 닉네임을 갱신한다. → UserProfile에 데이터를 반영할 때 어떤 메서드를 사용하나요?
  - Layer 및 Comment 테이블에서 해당 사용자의 authorEmail을 찾아 authorName을 새 닉네임으로 함께 갱신한다. → 프로필을 갱신한 뒤 다른 테이블에는 어떤 작업을 수행하나요?
  - 모든 작업이 성공하면 200 응답을 반환하고, 예외가 발생하면 500 에러를 반환한다. → 처리가 성공하거나 데이터베이스 에러가 발생했을 때 각각 어떤 응답이 전달되나요?
- 꼬리질문:
  - 같은 파일에 선언된 PATCH 함수와 이 POST 함수는 어떤 역할 차이가 있나요? (app/api/profile/route.ts::PATCH)
  - UserProfile 외에 Layer와 Comment 테이블까지 순차적으로 갱신하는 이유는 무엇인가요? (-)

## 피드 필터링 및 정렬 처리 방식 (`app/components/StudioFeed.tsx::StudioFeed`, 난이도 2, 기여 unverified)

- 요약: StudioFeed 컴포넌트는 API로부터 레이어 목록을 불러와 클라이언트 상태로 관리합니다. 검색어, 선택된 카테고리, 정렬 기준(최신순 또는 리스펙순)에 따라 레이어 목록을 클라이언트 메모리 상에서 직접 가공하여 렌더링합니다. 또한 작성자별 게시글 수에 따라 배지와 스타일을 계산하고 리스펙을 보내는 인터랙션을 제공합니다.
- 질문: 피드 검색과 정렬을 클라이언트에서 처리하도록 구성하신 이유가 무엇인가요?
- 취지: 서버 API에서 필터링과 정렬을 수행하지 않고 브라우저 상태를 이용해 가공하도록 구현한 의도와 트레이드오프를 묻는 질문입니다.
- 힌트: StudioFeed 내부의 fetchLayers와 processedLayers 변수가 데이터를 어떻게 다루는지 살펴보세요.
- 관련 심볼: app/components/StudioFeed.tsx::StudioFeed.fetchLayers, app/components/StudioFeed.tsx::StudioFeed.handleRespect, app/components/StudioFeed.tsx::StudioFeed.getLevelStyle
- 기대 포인트:
  - 전체 레이어 데이터를 fetchLayers에서 한 번에 가져와 브라우저 메모리에 저장한 뒤 processedLayers에서 연산하고 있음을 설명한다. → 현재 데이터 조회와 필터링 작업이 각각 어디에서 일어나고 있나요?
  - 클라이언트 필터링은 네트워크 요청을 줄이고 빠른 인터랙션을 제공하지만, 데이터 양이 늘어나면 성능 저하와 메모리 부하가 생길 수 있음을 인지한다. → 데이터 양이 매우 많아질 경우 현재 방식에서 어떤 문제가 발생할 수 있을까요?
  - authorCounts 계산 역시 전체 목록을 순회하여 작성자별 게시글 수를 집계하므로 데이터 규모에 영향을 받는 점을 언급한다. → 작성자별 레벨 스타일을 구하기 위한 집계 로직은 어떤 영향을 줄 수 있나요?
- 꼬리질문:
  - 데이터가 수천 개 이상으로 늘어난다면 fetchLayers 함수와 필터링 구조를 어떻게 개선하시겠습니까? (app/components/StudioFeed.tsx::StudioFeed.fetchLayers)
  - handleRespect 함수에서 서버 응답 성공 후 로컬 상태를 갱신하는데, 낙관적 업데이트 방식을 적용하지 않은 이유가 있나요? (app/components/StudioFeed.tsx::StudioFeed.handleRespect)

## 프로필 사진 업데이트 처리 (`app/api/profile/route.ts::PATCH`, 난이도 3, 기여 unverified)

- 요약: 세션에서 로그인 사용자의 이메일을 확인한 후 요청 본문의 이미지 URL로 사용자 프로필을 업데이트하거나 생성합니다. 인증 실패 시 401을 반환하며, 데이터베이스 작업 중 예외가 발생하면 500 응답을 반환합니다.
- 질문: 요청 본문 검증이나 DB 작업 실패 시 예외 처리가 어떻게 동작하나요?
- 취지: 요청 본문 파싱이나 DB 작업 과정에서 발생할 수 있는 오류와 예외 처리 방식에 대해 묻는 질문입니다.
- 힌트: PATCH 함수의 try catch 블록 안에서 request.json과 prisma 호출 부분을 살펴보세요.
- 관련 심볼: app/api/profile/route.ts::GET, app/api/profile/route.ts::POST
- 기대 포인트:
  - request.json() 호출 시 본문이 유효한 JSON이 아니거나 비어 있으면 파싱 에러가 발생해 catch 블록으로 이동한다. → 요청 본문이 올바른 JSON 형식이 아닐 때는 어떻게 처리되나요?
  - body.imageUrl에 대한 별도 유효성 검증이 없어 undefined나 올바르지 않은 값이 그대로 DB에 전달될 수 있다. → 전달받은 imageUrl 값의 유효성 검증은 어떻게 이루어지나요?
  - prisma.userProfile.upsert 실패를 포함한 모든 예외를 catch하여 콘솔에 로깅하고 500 상태 코드와 오류 메시지를 반환한다. → 데이터베이스 작업 중에 오류가 발생하면 클라이언트는 어떤 응답을 받나요?
- 꼬리질문:
  - 동일한 프로필을 다루는 GET 핸들러와 비교했을 때 인증 실패 시 응답 방식에 차이가 있나요? (app/api/profile/route.ts::GET)
  - 클라이언트가 잘못된 형식의 본문을 보냈을 때 500 대신 400 상태 코드를 반환하도록 개선할 수 있을까요? (-)
