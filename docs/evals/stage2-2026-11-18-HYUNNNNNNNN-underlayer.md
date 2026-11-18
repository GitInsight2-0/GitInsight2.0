# 2단계 분석 평가 (2026-11-18)

- 레포: `HYUNNNNNNNN/underlayer` @ `a0f0c277769b08c6d46f64b15a6b761a5249826b`
- 모델: `gemini/gemini-3.8-flash (reasoning_effort=low)`
- 주제 수: 6
- 기여 라벨: unverified 6개
- LLM 호출: 6회 (검증 실패 재요청 포함), 이 중 스키마 재시도 0회
- 비용: $0.0340
- 호출당 최대 지연: 9798 ms

사람이 확인할 것: 기대 포인트가 코드에 실제로 있는가, 질문이 존댓말 한 문장이고 자연스러운가,
힌트가 답을 알려 주지 않는가, 관련 심볼이 꼬리질문과 맞는가.

## MyProfile 컴포넌트 동작 (`app/components/MyProfile.tsx::MyProfile`, 난이도 1, 기여 unverified)

- 요약: 세션 정보를 바탕으로 사용자의 프로필 정보와 레이어 작성 통계를 서버에서 가져와 렌더링합니다. 닉네임 수정 및 프로필 이미지 업로드 요청을 서버 API로 전송하고 로컬 상태에 반영합니다. 레이어 개수에 따라 완성도 시각화 지표와 선호 카테고리를 계산하여 화면에 표시합니다.
- 질문: MyProfile 컴포넌트가 마운트될 때 어떤 순서로 데이터를 불러오나요?
- 취지: 컴포넌트가 처음 렌더링될 때 프로필과 통계 데이터를 조회하는 흐름을 알고 있는지 묻는 질문입니다.
- 힌트: 컴포넌트 내부의 useEffect 훅과 그 안에서 호출하는 비동기 함수들을 확인해 보세요.
- 관련 심볼: app/components/MyProfile.tsx::MyProfile.fetchProfile, app/components/MyProfile.tsx::MyProfile.fetchMyStats, app/components/MyProfile.tsx::MyProfile.handleImageUpload, app/components/MyProfile.tsx::MyProfile.handleSave
- 기대 포인트:
  - useEffect에서 세션의 사용자 이메일 존재 여부를 확인한 뒤 fetchProfile과 fetchMyStats를 호출한다. → 세션 정보를 확인한 뒤 어떤 함수들을 실행하나요?
  - fetchProfile은 사용자 이메일 쿼리로 프로필 API를 호출하여 닉네임과 이미지를 상태에 설정한다. → fetchProfile에서는 어떤 API를 호출하고 응답을 어떻게 처리하나요?
  - fetchMyStats는 전체 레이어 목록을 조회한 후 현재 사용자 이메일과 일치하는 항목만 필터링하여 myLayers에 저장한다. → fetchMyStats에서 가져온 전체 레이어 중 내 레이어는 어떻게 추려내나요?
- 꼬리질문:
  - handleImageUpload 함수에서는 선택된 이미지 파일을 어떤 절차로 서버에 전달하나요? (app/components/MyProfile.tsx::MyProfile.handleImageUpload)
  - handleSave 함수에서 닉네임을 변경할 때 유효성 검사와 요청 처리는 어떻게 진행되나요? (app/components/MyProfile.tsx::MyProfile.handleSave)

## 클라이언트 사이드 데이터 필터링 설계 (`app/components/MyCanvas.tsx::MyCanvas`, 난이도 2, 기여 unverified)

- 요약: MyCanvas 컴포넌트는 사용자의 레이어 목록을 조회하여 화면에 표시하고 통계와 시각 효과를 계산합니다. 또한 레이어 삭제, 공감 수 증가, 캔버스 영역의 이미지 캡처 및 다운로드 기능을 제공합니다. 데이터 조회 시 전체 레이어를 불러온 뒤 클라이언트에서 이메일 기준으로 필터링과 정렬을 수행합니다.
- 질문: 사용자의 레이어 목록을 클라이언트에서 필터링하도록 구현하신 이유가 궁금합니다.
- 취지: 서버 API에서 특정 사용자의 데이터만 조회하지 않고 전체를 받아와 브라우저에서 걸러내도록 만든 배경과 장단점을 묻는 질문입니다.
- 힌트: useEffect 안에서 실행되는 fetchMyLayers 함수 내부의 API 요청과 응답 처리 방식을 살펴보세요.
- 관련 심볼: app/components/MyCanvas.tsx::MyCanvas.fetchMyLayers, app/components/MyCanvas.tsx::MyCanvas.handleExportImage, app/components/MyCanvas.tsx::MyCanvas.handleDelete
- 기대 포인트:
  - 전체 목록을 조회한 뒤 클라이언트에서 filter로 거르면 데이터 양이 많아질수록 네트워크 낭비와 렌더링 성능 저하가 발생할 수 있음을 인식한다. → 전체 목록을 받아와서 브라우저에서 걸러내면 데이터가 늘어났을 때 어떤 문제가 생길 수 있을까요?
  - 서버 API 경로에 쿼리 파라미터를 추가하거나 세션 기반으로 서버에서 직접 필터링하여 반환하는 방식과의 차이점을 설명한다. → 이 데이터를 서버 API에서 직접 필터링해서 전달받는 방식과 비교하면 어떤 차이가 있을까요?
  - 보안 관점에서 다른 사용자의 레이어 정보가 브라우저에 그대로 노출될 수 있다는 점을 파악하고 있다. → 네트워크 응답으로 전체 데이터를 받아오는 것이 보안 측면에서는 어떤 영향을 줄 수 있나요?
- 꼬리질문:
  - handleExportImage에서 외부 라이브러리를 사용해 DOM을 캡처할 때 고려하셨던 제약 사항이 있나요? (app/components/MyCanvas.tsx::MyCanvas.handleExportImage)
  - handleDelete에서 삭제 성공 시 상태를 직접 변경하는데 SWR이나 React Query 같은 도구 대신 이 방식을 택하신 이유가 있나요? (app/components/MyCanvas.tsx::MyCanvas.handleDelete)

## 피드 로드 및 상태 동기화 (`app/components/StudioFeed.tsx::StudioFeed`, 난이도 3, 기여 unverified)

- 요약: StudioFeed 컴포넌트는 레이어 목록을 API로부터 가져와 필터링 및 정렬하여 렌더링합니다. 사용자는 레이어에 리스펙을 보낼 수 있으며 상태를 갱신합니다. 작성자별 게시글 수에 따라 단계별 스타일을 계산하여 시각적 배지를 제공합니다.
- 질문: 피드 로드나 리스펙 요청이 실패하면 화면에서 어떻게 처리되나요?
- 취지: 네트워크 통신 중 에러가 발생했을 때 사용자 경험과 상태 관리를 어떻게 다루고 있는지 묻는 질문입니다.
- 힌트: useEffect 내부의 fetchLayers와 handleRespect 함수의 try-catch 블록 및 응답 확인 부분을 확인해보세요.
- 관련 심볼: app/components/StudioFeed.tsx::StudioFeed.fetchLayers, app/components/StudioFeed.tsx::StudioFeed.handleRespect
- 기대 포인트:
  - fetchLayers에서 요청이 실패하거나 response.ok가 아닐 때 사용자에게 별도 에러 알림 없이 빈 목록과 로딩 해제 상태만 유지되는 점을 설명한다. → 초기 데이터를 불러오는 과정에서 오류가 나면 사용자는 어떤 화면을 보게 되나요?
  - handleRespect에서 PATCH 요청 성공 응답(response.ok)을 받은 뒤에만 낙관적이지 않게 로컬 상태를 갱신하고 에러 시 토스트 메시지를 띄우는 점을 말한다. → 리스펙 요청 시 서버 응답 상태에 따라 화면 데이터는 구체적으로 어떻게 갱신되나요?
  - handleRespect 실패 시 낙관적 갱신을 되돌릴 필요는 없으나, 네트워크 지연 상황에서 여러 번 클릭했을 때의 중복 요청 제어가 없다는 점을 짚는다. → 사용자가 버튼을 연속으로 빠르게 눌렀을 때 요청 처리가 어떻게 동작할까요?
- 꼬리질문:
  - handleRespect에서 상태를 갱신할 때 서버의 최신 데이터를 다시 동기화하지 않고 직접 1을 더하는데, 이 방식의 한계는 무엇인가요? (app/components/StudioFeed.tsx::StudioFeed.handleRespect)
  - fetchLayers 실행 시 response.ok가 false인 경우 catch 블록으로 가지 않는데, 이 상황은 어떻게 처리되나요? (app/components/StudioFeed.tsx::StudioFeed.fetchLayers)

## LayerDetailModal 데이터 흐름 (`app/components/LayerDetailModal.tsx::LayerDetailModal`, 난이도 1, 기여 unverified)

- 요약: LayerDetailModal은 선택한 레이어의 상세 정보, 연결된 실패 기록, 댓글을 보여주는 컴포넌트입니다. 현재 선택된 레이어 상태에 따라 댓글과 연관된 레이어 목록을 불러옵니다. 댓글 작성 및 관리자 계정에 대한 삭제 기능도 처리합니다.
- 질문: LayerDetailModal에서 레이어가 변경될 때 데이터를 어떤 순서로 불러오나요?
- 취지: 모달이 열리거나 다른 레이어로 전환될 때 필요한 데이터들을 어떻게 조회하고 상태를 갱신하는지 묻는 질문입니다.
- 힌트: 컴포넌트 상단의 useEffect 훅들과 그 내부의 fetchData 함수 구현부를 살펴보세요.
- 관련 심볼: app/components/LayerDetailModal.tsx::LayerDetailModal.fetchData, app/components/LayerDetailModal.tsx::LayerDetailModal.handleCommentSubmit, app/components/LayerDetailModal.tsx::LayerDetailModal.handleAdminDelete
- 기대 포인트:
  - currentLayer가 변경될 때 fetchData가 실행되어 댓글 조회와 연관 레이어 조회가 순서대로 진행된다고 설명한다. → currentLayer 상태가 바뀔 때 어떤 부수 효과가 실행되어 데이터를 가져오나요?
  - 카테고리가 '🌟 최종 성공 (Masterpiece)'이고 linkedLayerIds가 존재할 때만 전체 레이어 목록에서 연결된 실패 카드들을 필터링해 가져온다고 설명한다. → 연결된 실패 기록들은 어떤 조건에서 어떻게 가져오나요?
  - props로 전달받은 initialLayer가 달라지면 currentLayer를 initialLayer로 동기화한다고 설명한다. → 모달 외부에서 initialLayer가 바뀌면 currentLayer는 어떻게 반영되나요?
- 꼬리질문:
  - fetchData 안에서 연관 레이어를 가져올 때 전체 레이어 API를 호출하는데, 다른 방법도 고려해보셨나요? (app/components/LayerDetailModal.tsx::LayerDetailModal.fetchData)
  - handleCommentSubmit 완료 후 댓글 목록을 다시 불러오는 과정은 어떻게 동작하나요? (app/components/LayerDetailModal.tsx::LayerDetailModal.handleCommentSubmit)

## QuickSketch 폼 설계 (`app/components/QuickSketch.tsx::QuickSketch`, 난이도 2, 기여 unverified)

- 요약: QuickSketch 컴포넌트는 사용자의 상황, 행동, 교훈 및 이미지 입력을 받아 새 기록을 생성하는 폼입니다. 최종 성공 카테고리를 선택하면 과거 실패 기록 목록을 불러와 체크박스로 선택할 수 있게 합니다. 선택된 과거 실패 기록의 내용과 식별자를 폼 데이터와 함께 서버로 전송합니다.
- 질문: 성공 모드에서 과거 실패 기록을 불러오고 연동하는 방식을 어떻게 설계하셨나요?
- 취지: 특정 카테고리를 선택했을 때 이전 데이터를 조회하고 새 데이터와 엮는 흐름의 의도를 묻는 질문입니다.
- 힌트: formData.category의 값에 따른 useEffect 동작과 handleCheckboxToggle 함수를 살펴보세요.
- 관련 심볼: app/components/QuickSketch.tsx::QuickSketch.handleCheckboxToggle, app/components/QuickSketch.tsx::QuickSketch.handleSubmit
- 기대 포인트:
  - formData의 category 상태가 변경될 때 useEffect를 통해 과거 기록을 비동기 조회하여 pastFailures에 저장하는 구조를 설명한다. → 특정 카테고리일 때만 과거 기록을 가져오도록 처리한 과정을 설명해 주실 수 있을까요?
  - handleCheckboxToggle을 통해 선택된 실패 기록들의 정보를 텍스트 필드(action)에 서식화하여 주입하고 식별자 목록을 관리하는 방식을 설명한다. → 체크박스를 선택했을 때 텍스트 영역에 내용이 자동으로 채워지는 동작은 어떻게 구현하셨나요?
  - handleSubmit에서 선택된 ID 목록을 문자열로 직렬화하여 API 요청 본문에 담아 전송하는 설계를 설명한다. → 선택된 실패 기록 정보가 폼 제출 시 백엔드로 어떤 형태로 전달되나요?
- 꼬리질문:
  - handleCheckboxToggle에서 텍스트를 덮어쓰면 사용자가 직접 입력한 내용이 유실될 수 있는데, 이를 어떻게 고려하셨나요? (app/components/QuickSketch.tsx::QuickSketch.handleCheckboxToggle)
  - 전체 목록을 조회한 뒤 클라이언트에서 필터링하는 방식 대신 백엔드 쿼리를 활용하는 방법은 고민해 보셨나요? (-)

## UserCanvas 데이터 처리와 예외 (`app/components/UserCanvas.tsx::UserCanvas`, 난이도 3, 기여 unverified)

- 요약: UserCanvas는 특정 사용자의 레이어 목록을 불러와 시간순으로 정렬해 타임라인 형태로 보여주는 컴포넌트입니다. 사용자가 카드를 클릭하면 상세 모달을 띄우고 모달 내 리스펙 클릭 시 화면 상태를 갱신합니다.
- 질문: 네트워크 요청 실패나 데이터 누락 시 화면 처리가 어떻게 설계되어 있나요?
- 취지: 요청이 실패하거나 예상치 못한 데이터 상황에서 컴포넌트가 어떻게 반응하는지 확인하려는 질문입니다.
- 힌트: useEffect 내부의 데이터 호출 로직과 상태 업데이트 흐름을 확인해 보세요.
- 관련 심볼: app/components/UserCanvas.tsx::UserCanvas.fetchUserLayers, app/components/UserCanvas.tsx::UserCanvas.handleRespect
- 기대 포인트:
  - fetchUserLayers에서 오류 발생 시 콘솔 로그만 남기고 에러 화면 표시 없이 로딩만 해제되는 점을 짚는다. → 네트워크 통신 중 예외가 발생하면 사용자 화면에는 어떤 모습으로 나타나나요?
  - handleRespect 함수가 서버 API 요청 없이 로컬 상태만 갱신하여 새로고침 시 변경 사항이 사라지는 구조임을 인지한다. → 리스펙을 보냈을 때 서버와의 동기화는 어떻게 처리되고 있나요?
  - 전체 레이어 목록을 가져와 클라이언트에서 이메일로 필터링하는 방식의 대용량 데이터 부하 문제를 설명한다. → 전체 레이어 데이터양이 매우 많아질 때 발생할 수 있는 병목은 무엇인가요?
- 꼬리질문:
  - fetchUserLayers 함수에서 응답 상태가 정상이 아닐 때 로딩이 끝나면 어떤 화면이 표시되나요? (app/components/UserCanvas.tsx::UserCanvas.fetchUserLayers)
  - handleRespect 함수는 서버 통신 실패에 대한 롤백 로직이 없는데 어떤 한계가 있나요? (app/components/UserCanvas.tsx::UserCanvas.handleRespect)
