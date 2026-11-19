# 2단계 분석 평가 (2026-11-19)

- 레포: `GitInsight2-0/GitInsight2.0` @ `fd39d020d35397e398a7e078b765d84cb0bdb6cd`
- 모델: `gemini/gemini-3.8-flash (reasoning_effort=low)`
- 주제 수: 6
- 기여 라벨: unverified 6개
- LLM 호출: 6회 (검증 실패 재요청 포함), 이 중 스키마 재시도 0회
- 비용: $0.0424
- 호출당 최대 지연: 11170 ms

## 왜 돌렸나

#114로 TypeScript·JavaScript 심볼 추출이 들어왔습니다.
#114의 평가(`stage2-2026-11-18-HYUNNNNNNNN-underlayer.md`)는 TS만 있는 레포였고, Python과 TS가 섞인 레포는 아직 아무도 돌리지 않았습니다.
팀 저장소(Python 서버·워커와 TSX 웹)를 main `fd39d02`로 분석해, 두 언어 후보가 어떻게 섞여 나오는지와 질문 품질을 봤습니다.

요청자를 주지 않아 기여 라벨은 모두 `unverified`입니다.
요청자를 주면 기여 라벨 순서가 먼저라 본인 코드가 앞에 오므로, 언어끼리의 순서만 보려고 뺐습니다.

## 사람이 확인한 결과 (A, 코드와 대조)

- **사실 확인**: 6개 주제의 요점과 꼬리질문을 main 코드와 대조했습니다. **코드와 어긋나는 요점은 없습니다**
  - 설계 이유를 묻는 요점(`GrowthTile`의 빈 구간 처리, `startVoiceSession`의 위임 누락)은 코드 주석에 적힌 이유와 같습니다
  - 표현이 조금 넓은 것 1건: `InterviewBrain` p1 "분산 락(locked)". 실제로는 Postgres 행 잠금(`SELECT … FOR UPDATE`)이라 여러 서버 프로세스 사이에서도 동작하지만, 별도 분산 락 도구는 아닙니다
  - 이유를 덧붙인 것 1건: `startVoiceSession` p3 꼬리질문 "네트워크 지연 등으로 늦게 도착한 이전 턴의 받아쓰기". 코드 주석은 "이미 서버로 보낸 턴의 늦은 받아쓰기"라고만 하고 원인은 적지 않았습니다. 지난 평가(`stage2-2026-11-18-gitinsight.md`)에서 본 "동작 뒤에 이유를 덧붙이는" 유형과 같지만, 답하는 데 방해되지는 않습니다
- **언어 비율이 한쪽으로 쏠림** (이번에 새로 본 것)
  - 심볼 1344개 중 Python 1182개(88%), TS 157개, JS 5개입니다. 주제 후보 150개 중에서도 Python이 113개(75%)입니다
  - 그런데 앞 6자리는 TS 5개, Python 1개입니다. 후보 앞 12자리의 언어 순서는 T T T P T T T P T P T P입니다
  - 원인: 후보를 전체 줄 수가 많은 순으로 고릅니다. TS는 컴포넌트와 클로저 안에 함수를 모아 두어 한 심볼이 길고(`InterviewScreen` 611줄, `startVoiceSession` 410줄), Python은 클래스와 메서드로 나뉘어 짧습니다
  - #114의 "컴포넌트 파일과 나머지 번갈아 놓기"는 `.tsx`·`.jsx`와 나머지를 나눕니다. `.ts`는 나머지 쪽이라 Python과 길이로 경쟁해서 앞자리를 차지합니다
  - 워커(`worker/`) 코드는 6개 주제에 하나도 없습니다. 이 레포의 핵심인 분석 엔진과 면접 두뇌 중 두뇌 하나만 나왔습니다
- **긴 코드가 잘림**: 6개 중 4개가 8,000자를 넘어 앞부분만 LLM에 들어갔습니다
  - `InterviewScreen` 91–701줄 중 313줄까지, `startVoiceSession` 128–537줄 중 341줄까지, `VoiceLab` 22–296줄 중 236줄까지, `InterviewBrain` 73–369줄 중 273줄까지입니다
  - 요점과 꼬리질문은 모두 보인 범위 안의 코드만 다뤄서, 안 보인 코드를 지어낸 것은 없습니다. 대신 컴포넌트 뒤쪽(음성 처리, 화면 그리기)은 질문 재료가 되지 못했습니다
  - 이 문제는 #119(해커톤 뒤)에서 다룹니다
- **실험 화면이 주제가 됨**: `VoiceLab`은 음성 측정용 실험 화면(`/lab/voice`)입니다. 경로 규칙(`is_inactive_path`)은 `legacy`, `example` 같은 단어만 보고 `lab`은 모릅니다. 이 레포에만 있는 이름이라 규칙으로 넣을지는 더 지켜봅니다
- **나머지**
  - 질문은 모두 존댓말 한 문장이고 자연스럽습니다. `[A]` 같은 주석 표시, 줄 번호, 내부 용어가 없고 검증 재요청도 0회입니다
  - TS 주제의 관련 심볼과 꼬리질문은 #114가 내보내는 안쪽 함수(`InterviewScreen.requestReport`, `connectGemini.orAbort` 등)를 잘 가리킵니다
- **결론**: 섞인 레포에서도 질문 품질과 사실 정확도는 Python 레포와 비슷합니다. 다만 요청자 없이 분석하면 길이가 긴 TS 코드가 앞자리를 차지해서, 레포의 주 언어(Python)가 주제에서 거의 빠집니다
- **다음에 검토할 것** (해커톤 뒤)
  - 후보 순서를 전체 줄 수 대신 동작 줄 수(`logic_line_count`, `script_logic_line_count`)로 정하거나, 언어끼리 번갈아 놓기
  - 실제 사용(로그인한 요청자)에서는 기여 라벨 순서가 먼저라 쏠림이 덜할 수 있습니다. 요청자를 준 평가로 한 번 더 확인합니다

사람이 확인할 것: 기대 포인트가 코드에 실제로 있는가, 질문이 존댓말 한 문장이고 자연스러운가,
힌트가 답을 알려 주지 않는가, 관련 심볼이 꼬리질문과 맞는가.

## 면접 화면 진행 흐름 (`web/components/InterviewScreen.tsx::InterviewScreen`, 난이도 1, 기여 unverified)

- 요약: InterviewScreen은 모의면접 진행 상태와 음성 및 텍스트 모드를 관리하는 컴포넌트입니다. 세션 생성 후 사용자의 턴 입력을 서버로 전송하고 질문 응답을 화면에 기록합니다. 면접이 종료 상태가 되면 완료 처리를 거쳐 서버에 결과 리포트를 요청합니다.
- 질문: InterviewScreen에서 면접 세션이 시작되고 진행되는 전체 흐름을 설명해 주세요.
- 취지: 면접 화면 컴포넌트가 세션을 생성하고 사용자의 입력을 받아 다음 질문을 처리하는 순서를 묻는 질문입니다.
- 힌트: startText부터 submit, 그리고 면접 종료 시 호출되는 requestReport 함수의 흐름을 살펴보세요.
- 관련 심볼: web/components/InterviewScreen.tsx::InterviewScreen.startText, web/components/InterviewScreen.tsx::InterviewScreen.requestReport, web/components/InterviewScreen.tsx::InterviewScreen.endInterview
- 기대 포인트:
  - startText 함수를 통해 analysisId를 전달하여 세션을 생성하고 첫 질문을 받아 상태 및 히스토리에 설정합니다. → 텍스트 면접을 시작할 때 세션 생성과 첫 질문 처리는 어떤 순서로 이루어지나요?
  - submit 함수에서 입력 텍스트와 turn_id를 담아 서버로 턴을 전송하고, 반환된 다음 질문 발화를 히스토리와 상태에 반영합니다. → 사용자가 텍스트 답변을 제출했을 때 서버 통신과 화면 갱신은 어떻게 처리되나요?
  - 응답의 done 속성이 참이 되거나 사용자가 끝내기를 선택하면 closeLocally 등을 거쳐 requestReport로 리포트 생성을 요청합니다. → 면접이 끝나는 조건이 되었을 때 리포트 생성 요청은 어떻게 이어지나요?
- 꼬리질문:
  - 텍스트 답변을 전송할 때 turnId를 생성하거나 재사용하는 기준은 무엇인가요? (-)
  - endInterview를 호출해 면접을 직접 끝낼 때는 어떤 상태 정리 작업들이 진행되나요? (web/components/InterviewScreen.tsx::InterviewScreen.endInterview)
  - requestReport에서 중복 요청을 방지하기 위해 어떤 방식을 사용하나요? (web/components/InterviewScreen.tsx::InterviewScreen.requestReport)

## 음성 세션 수명 관리와 위임 누락 방지 (`web/lib/voice/session.ts::startVoiceSession`, 난이도 2, 기여 unverified)

- 요약: startVoiceSession은 브라우저에서 음성 면접을 시작하고 세션 수명 주기, 오디오 미터링, 음성 인식 버퍼를 제어하는 함수입니다. 음성 모델의 도구 호출 및 발화 상태를 추적하고, 모델이 서버로 위임하지 않고 직접 답변하는 위임 누락을 감지하여 처리합니다. 사용자의 중단 신호나 턴 종료 조건에 맞춰 타이머와 리소스를 정리하고 세션을 종료합니다.
- 질문: 음성 세션에서 음성 AI가 서버를 거치지 않고 임의로 답하는 상황을 어떻게 감지하고 대응하도록 설계하셨나요?
- 취지: 면접용 두뇌 서버와 음성 AI의 역할을 분리하기 위해 음성 인식 상태와 도구 호출 타이밍을 비교해 판단하는 구조를 묻습니다.
- 힌트: onAiTranscript 함수 내부에서 userSpokeSinceTool 상태를 확인하고 버퍼를 비우는 로직을 살펴보세요.
- 관련 심볼: web/lib/voice/session.ts::startVoiceSession.onAiTranscript, web/lib/voice/session.ts::startVoiceSession.onUserTranscript, web/lib/voice/session.ts::startVoiceSession.shutdown
- 기대 포인트:
  - 사용자가 발화한 뒤 도구 호출 없이 AI 모델이 말을 마쳤을 때 이를 위임 누락으로 감지하고 화면과 메트릭에 오류를 기록한다. → 사용자의 발화와 음성 AI의 응답 타이밍을 코드가 어떻게 대조해 보나요?
  - 위임 누락이 발생하면 모델이 독자적으로 처리한 받아쓰기 텍스트 버퍼를 비우고 이미 보낸 키로 등록해 다음 턴 답변에 섞이지 않게 차단한다. → 위임 누락이 일어났을 때 기존에 모아 두었던 받아쓰기 버퍼는 어떻게 정리하나요?
  - 턴별 식별 키와 집합을 사용해 이미 서버로 전송된 턴의 뒤늦은 받아쓰기 텍스트가 다음 턴으로 오염되는 것을 방지한다. → 네트워크 지연 등으로 늦게 도착한 이전 턴의 받아쓰기 데이터는 어떻게 격리하나요?
- 꼬리질문:
  - onAiTranscript에서 interrupted 상태의 발화를 위임 누락 판단에서 제외한 이유는 무엇인가요? (web/lib/voice/session.ts::startVoiceSession.onAiTranscript)
  - shutdown이 실행될 때 등록된 타이머들과 미터링 객체는 어떤 순서로 정리되나요? (web/lib/voice/session.ts::startVoiceSession.shutdown)

## 음성 세션 수명주기와 예외 처리 (`web/components/VoiceLab.tsx::VoiceLab`, 난이도 3, 기여 unverified)

- 요약: VoiceLab은 실시간 음성 세션을 시작하여 지연 시간, 끼어들기, 위임 누락 등의 성능 지표를 측정하는 컴포넌트입니다. 세션 시작 중 중단 신호나 예외가 발생하면 오류를 로그에 남기고 상태를 실패로 전환하며 리소스를 정리합니다. 페이지 언마운트나 수동 중지 시 AbortController와 세션 정리 메서드를 호출해 안전하게 연결을 해제합니다.
- 질문: 음성 세션 연결 도중 실패하거나 사용자가 중단할 때 어떻게 처리되나요?
- 취지: 음성 세션을 시작하거나 진행하는 과정에서 오류가 발생하거나 중단 요청이 들어왔을 때, 예외를 어떻게 구분하고 리소스를 정리하는지 묻는 질문입니다.
- 힌트: VoiceLab 내부의 start 함수와 stop 함수, 그리고 useEffect의 반환 함수를 살펴보세요.
- 관련 심볼: web/components/VoiceLab.tsx::VoiceLab.start, web/components/VoiceLab.tsx::VoiceLab.stop
- 기대 포인트:
  - start 함수 내에서 try-catch를 통해 AbortController에 의한 취소는 일반 오류와 분리하여 처리하고 로그만 남긴다. → 사용자가 세션 시작 도중 취소했을 때와 일반 네트워크 오류가 났을 때 로그 기록 방식이 어떻게 다른가요?
  - 일반 예외가 발생한 경우에는 explainError로 오류를 설명 로그에 남기고 상태를 실패로 변경하며 실행 상태를 종료한다. → 예외가 발생했을 때 화면 상태와 실행 플래그는 어떻게 정리되나요?
  - 페이지 언마운트 시 useEffect의 클린업 함수에서 abort 신호를 호출해 잔여 연결을 정리한다. → 세션 진행 중에 사용자가 화면을 벗어나면 연결 정리는 어떻게 보장되나요?
  - stop 함수 호출 시 진행 중인 abort 신호 전송과 session의 stop 호출을 모두 수행하고 참조를 null로 비운다. → 수동으로 세션을 중단할 때 session 참조와 abort 신호는 어떤 절차를 거치나요?
- 꼬리질문:
  - start 함수가 실행 중인 상태에서 다시 호출되지 않도록 방지하는 장치는 어떻게 되어 있나요? (web/components/VoiceLab.tsx::VoiceLab.start)
  - stop 함수에서 session 참조를 null로 초기화하는데, 이와 별개로 start의 ended 콜백에서도 정리하는 이유는 무엇인가요? (web/components/VoiceLab.tsx::VoiceLab.stop)

## InterviewBrain 면접 진행 흐름 (`api/src/gitinsight_api/brain/service.py::InterviewBrain`, 난이도 1, 기여 unverified)

- 요약: InterviewBrain은 면접 세션의 생성 및 턴별 대화를 조율하는 핵심 클래스입니다. 세션 시작 시 분석 결과를 조회하고 질문 계획을 세워 초기 상태를 DB에 저장하며, 턴 단위 요청에서는 잠금을 걸고 이전 턴 중복 검사, LLM 기반 답변 평가, 규칙 기반 다음 액션 결정을 순차적으로 수행합니다.
- 질문: InterviewBrain에서 한 턴의 면접이 처리되는 과정을 순서대로 설명해 주세요.
- 취지: 사용자의 발화가 들어왔을 때 내부에서 어떤 단계를 거쳐 다음 질문이나 응답을 결정하는지 기본적인 처리 흐름을 묻는 질문입니다.
- 힌트: handle_turn 메서드 내부에서 세션 락을 얻고 응답을 만들어 커밋하기까지의 과정을 살펴보세요.
- 관련 심볼: api/src/gitinsight_api/brain/service.py::InterviewBrain.handle_turn, api/src/gitinsight_api/brain/service.py::InterviewBrain._judge, api/src/gitinsight_api/brain/service.py::InterviewBrain.start
- 기대 포인트:
  - 세션 레포지토리에서 세션을 조회한 뒤 중복 턴 방지를 위해 분산 락(locked)을 획득하고 기존 턴 기록을 확인한다. → 턴 요청이 들어왔을 때 가장 먼저 세션 상태와 잠금을 어떻게 다루나요?
  - 남은 시간을 확인한 후 시간이 남아 있다면 LLM을 호출하는 _judge 메서드를 통해 지원자 발화의 충족 여부를 평가한다. → 지원자의 답변 평가는 어떤 조건에서 어떤 방식으로 진행되나요?
  - 평가 결과와 남은 시간, 준비된 질문 수 등을 RuleInput으로 묶어 decide 함수로 넘겨 규칙 기반으로 다음 액션을 결정한다. → 답변 평가 후 다음 액션(질문, 꼬리질문 등)은 어떻게 결정되나요?
  - 결정된 액션을 바탕으로 응답(BrainReply)을 생성하고 턴 레코드(TurnRecord)를 저장한 뒤 커밋한다. → 액션이 결정된 후 세션 상태 갱신과 턴 저장은 어떻게 마무리되나요?
- 꼬리질문:
  - handle_turn 메서드에서 세션 레포지토리의 잠금을 걸기 전에 분석 결과를 먼저 조회하는 이유는 무엇인가요? (api/src/gitinsight_api/brain/service.py::InterviewBrain.handle_turn)
  - start 메서드에서 review_only 옵션이 켜져 있을 때는 어떤 순서로 동작하나요? (api/src/gitinsight_api/brain/service.py::InterviewBrain.start)
  - 지원자의 발화 텍스트가 비어 있거나 장애 대비 플래그가 켜져 있을 때 _judge는 어떻게 처리하나요? (api/src/gitinsight_api/brain/service.py::InterviewBrain._judge)

## 미평가 구간 차트 처리 (`web/components/GrowthChart.tsx::GrowthTile`, 난이도 2, 기여 unverified)

- 요약: GrowthTile은 각 역량별 면접 점수 추이를 SVG 차트로 시각화하는 컴포넌트입니다. 평가 점수가 없는 면접 회차는 실선으로 잇지 않고 분리하며, 보간된 회색 선과 투명한 히트 영역을 두어 빈 구간도 탐색할 수 있게 합니다. ResizeObserver를 통해 컨테이너 너비에 맞게 차트 크기를 반응형으로 조정합니다.
- 질문: 평가 점수가 없는 구간의 차트 선을 끊고 별도로 처리하신 이유가 무엇인가요?
- 취지: 평가 데이터가 비어 있는 구간을 왜 연속된 실선으로 그리지 않고 별도의 시각 요소와 상호작용으로 설계했는지 묻습니다.
- 힌트: 코드 중간에 gaps와 runs 배열을 구성하고 선을 그리는 SVG 렌더링 부분을 살펴보세요.
- 관련 심볼: web/components/GrowthChart.tsx::missingText, web/components/GrowthChart.tsx::GrowthTile.x, web/components/GrowthChart.tsx::pointText
- 기대 포인트:
  - 평가받지 않은 면접에 임의의 점수를 부여하거나 왜곡하지 않기 위해 실선(runs)을 끊고 실제 평가된 점만 연결함을 설명한다. → 평가 없는 구간을 연속된 실선으로 바로 잇지 않은 이유가 무엇인가요?
  - 빈 구간(gaps)을 보간한 회색 선으로 잇고 점(dot)을 찍지 않되, 해당 위치에 가상 히트 영역(missing)을 두어 탐색할 수 있게 한 설계를 말한다. → 평가가 없는 회차는 사용자에게 어떻게 표시되고 상호작용하도록 구현하셨나요?
  - 실제 점수가 아님에도 사용자가 마우스나 키보드 포커스로 해당 회차를 가리켰을 때 '평가 없음' 정보를 명확히 인지할 수 있도록 유도한 접근성을 설명한다. → 평가가 없는 회차를 가리켰을 때 정보 전달은 어떻게 처리하셨나요?
- 꼬리질문:
  - 평가 없는 구간의 상호작용 원을 만들 때 missingText를 사용하셨는데, 어떤 내용을 사용자에게 알려주나요? (web/components/GrowthChart.tsx::missingText)
  - 화면 폭에 따라 x좌표를 계산하는 로직에서 너비 변화를 어떻게 감지하고 갱신하나요? (web/components/GrowthChart.tsx::GrowthTile.x)

## Gemini 음성 연결 수명주기 관리 (`web/lib/voice/gemini.ts::connectGemini`, 난이도 3, 기여 unverified)

- 요약: 마이크 스트림과 AudioWorklet을 이용해 사용자 오디오를 캡처하고, WebSocket을 통해 Gemini 실시간 API와 통신합니다. 응답으로 오는 오디오 데이터를 큐에 넣어 재생하며, 취소 신호나 통신 에러 발생 시 모든 자원을 안전하게 해제합니다.
- 질문: 연결 진행 중 취소 신호가 들어오면 자원 정리가 어떻게 처리되나요?
- 취지: 비동기 연결 과정에서 중단 이벤트가 발생했을 때 리소스 누수를 막기 위한 처리 흐름을 묻는 질문입니다.
- 힌트: connectGemini 내부의 orAbort 구현과 micRequest 처리, cleanup 함수를 살펴보세요.
- 관련 심볼: web/lib/voice/gemini.ts::connectGemini.cleanup, web/lib/voice/gemini.ts::connectGemini.orAbort, web/lib/voice/gemini.ts::connectGemini.flush
- 기대 포인트:
  - orAbort 유틸 함수와 AbortSignal 이벤트를 연결해 비동기 대기 중에도 즉시 에러를 발생시키고 정리를 시작함을 설명한다. → 비동기 단계마다 작업 중단을 즉각 감지하기 위해 어떤 방식을 사용하셨나요?
  - getUserMedia 승인 대기 중 취소되더라도 이후 들어온 스트림의 트랙을 중단하여 마이크가 켜진 채 남지 않게 방어한 점을 설명한다. → 사용자가 권한 허용 창을 띄워둔 상태에서 작업이 취소되면 마이크 스트림은 어떻게 되나요?
  - cleanup 함수를 통해 WebSocket 종료, 재생 버퍼 플러시, 마이크 트랙 정지, 오디오 컨텍스트 닫기를 일관되게 수행함을 설명한다. → cleanup 함수에서는 구체적으로 어떤 자원들을 해제하고 있나요?
- 꼬리질문:
  - orAbort 함수는 중단 신호가 발생했을 때 프로미스 경합을 어떻게 처리하나요? (web/lib/voice/gemini.ts::connectGemini.orAbort)
  - flush 함수가 호출되면 이미 재생 중이거나 대기 중인 오디오 소스는 어떻게 처리되나요? (web/lib/voice/gemini.ts::connectGemini.flush)
