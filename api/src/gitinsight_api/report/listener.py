"""AI 청자 테스트 (#111): 코드를 본 적 없는 LLM이 지원자의 답변만 읽고 그 코드에 대한 객관식 문제를 풉니다.

정답(기대 요점)을 아는 채점자는 답변의 빈틈을 스스로 메우기 쉽습니다.
코드를 모르는 청자는 메울 수 없어서, 실제로 말한 내용만 정답으로 이어집니다.

- 문제는 면접에서 물어본 주제의 기대 요점마다 하나씩 LLM이 만듭니다. 이 호출에는 답변 기록을 주지 않습니다.
  주제마다 따로, 동시에 부릅니다. 한 번에 만들면 주제 6개(15문제)에서 23초가 걸려 제한 시간을 넘겼습니다.
  한 주제의 호출이 실패하면 그 주제의 문제만 빠집니다.
- 청자 호출에는 문제와 지원자의 답변 자막만 줍니다. 코드, 주제 자료, 면접관의 질문과 힌트는 주지 않습니다.
- 청자는 같은 규칙으로 두 번 풉니다. 답변 기록을 읽고 한 번, 답변 기록 없이 한 번(대조군)입니다.
  청자는 설명에 근거가 없으면 "알 수 없음"을 고릅니다. 대조군에서도 맞힌 문제는 설명 없이 답이 드러나는 문제입니다.
  짐작해서 풀게 한 대조군은 쓰지 않습니다. 실제 모델이 문제만 보고 12문제 중 9~10문제를 맞혀서,
  그 값을 빼면 설명해서 맞힌 몫까지 지워졌습니다 (docs/evals/listener-2026-11-18.md).
- 보기 순서, "알 수 없음" 보기, 채점은 서버가 정합니다. 역량 수준(점수)에는 쓰지 않습니다.
- 기여 라벨은 프롬프트에 넣지 않습니다.
- 어느 단계든 실패하거나 제한 시간을 넘으면 None을 돌려주고, 리포트는 청자 테스트 없이 나갑니다.
"""

import asyncio
import json
import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass

from gitinsight_api.llm import LLMClient, LLMError, Message
from gitinsight_api.llm.untrusted import UNTRUSTED_NOTICE, untrusted_block
from gitinsight_contracts.analysis import AnalysisResult
from gitinsight_contracts.llm import ListenerAnswers, QuizDraft
from gitinsight_contracts.report import ListenerQuestion, ListenerTest

logger = logging.getLogger(__name__)

QUIZ_PURPOSE = "listener_quiz"
LISTENER_PURPOSE = "listener"
UNKNOWN_CHOICE = "알 수 없음"
DISTRACTOR_COUNT = 3
# 기대 요점 하나에 문제 하나입니다. 주제 순서대로 이만큼만 냅니다.
MAX_QUESTIONS = 15
# 쓸 수 있는 문제가 이보다 적으면 숫자가 의미 없어서 테스트를 하지 않습니다.
MIN_QUESTIONS = 3
MAX_QUESTION_CHARS = 300
MAX_CHOICE_CHARS = 200

QUIZ_SYSTEM_PROMPT = f"""당신은 코드 설명이 잘 전달됐는지 확인하는 객관식 문제를 만듭니다.
주제 자료의 요점(points)마다 문제를 하나씩 만드세요.
이 문제는 그 코드를 본 적 없는 사람이, 개발자의 말로 된 설명만 듣고 풉니다.

규칙:
- 문제 하나는 요점 하나만 확인합니다. topic_id와 point_id는 주제 자료의 값을 그대로 씁니다.
- 문제에는 무엇에 대한 질문인지 알 수 있게 함수나 클래스 이름을 적습니다.
- correct는 그 요점이 말하는 내용입니다. 요점에 없는 내용을 지어내지 마세요.
- 문제 문장은 무엇을 묻는지만 말합니다. 코드가 어떻게 동작하는지, 어떤 문제가 있는지를 문제 문장에서 미리 말하지 마세요.
- distractors는 오답 {DISTRACTOR_COUNT}개입니다. 이 코드를 본 적 없는 숙련된 개발자가 정답만큼 그럴듯하다고 느낄, 실제로 흔히 쓰는 다른 구현 방식이어야 합니다. 정답보다 더 일반적이거나 더 권장되는 방식일수록 좋습니다.
- 오답은 정답과 분명히 다른 내용이어야 합니다. 정답과 함께 참일 수 있는 보기는 만들지 마세요.
- 정답과 오답은 길이, 말투, 구체적인 정도를 맞춥니다. 정답에 이름이나 숫자가 있으면 오답에도 같은 수준의 이름이나 숫자를 넣습니다.
- 설명을 듣지 않은 사람이 상식으로 정답을 고를 수 없어야 합니다. 다른 문제의 문장이나 보기가 정답을 알려 주면 안 됩니다.
- "알 수 없음", "모두 맞음", "모두 틀림" 같은 보기는 만들지 마세요.
- 문제와 보기는 한국어 한 문장으로 짧게 씁니다.

{UNTRUSTED_NOTICE}"""

LISTENER_SYSTEM_PROMPT = f"""당신은 이 코드를 본 적 없는 청자입니다.
어떤 개발자가 자기 코드를 말로 설명한 기록(음성 인식 자막)만 읽고, 그 코드에 대한 객관식 문제를 풉니다.

규칙:
- 설명에서 알 수 있는 내용만으로 답을 고릅니다.
- 설명에 근거가 없는 문제는 상식이나 짐작으로 고르지 말고 "{UNKNOWN_CHOICE}" 보기를 고릅니다. 설명 기록이 없으면 모든 문제가 그렇습니다.
- 설명이 맞는지는 판단하지 않습니다. 설명이 말한 대로 고릅니다.
- "모르겠다"는 말이나 질문을 되묻는 말은 근거가 아닙니다.
- 문제마다 answers에 하나씩 답합니다. number는 문제 번호, choice는 고른 보기의 번호입니다.
- 한 문제에 보기 하나만 고릅니다.

{UNTRUSTED_NOTICE}"""


@dataclass(frozen=True)
class QuizQuestion:
    topic_id: str
    point_id: str
    question: str
    # 마지막 보기는 항상 "알 수 없음"입니다.
    choices: tuple[str, ...]
    correct_index: int

    @property
    def unknown_index(self) -> int:
        return len(self.choices) - 1


def quiz_points(analysis: AnalysisResult, topic_ids: Sequence[str]) -> list[tuple[str, str]]:
    """문제를 낼 (topic_id, point_id)를 주제 순서대로 돌려줍니다."""
    points = [
        (topic_id, point.point_id)
        for topic_id in topic_ids
        for point in analysis.topic(topic_id).expected_points
    ]
    return points[:MAX_QUESTIONS]


def build_quiz_messages(
    analysis: AnalysisResult, topic_ids: Sequence[str], only_topic_id: str | None = None
) -> list[Message]:
    """only_topic_id를 주면 그 주제의 자료만 넣습니다. 문제 수 상한은 topic_ids 전체 기준입니다."""
    wanted = set(quiz_points(analysis, topic_ids))
    topics = [
        {
            "topic_id": topic.topic_id,
            "title": topic.title,
            "summary": topic.summary,
            "symbols": [s.name for s in (topic.symbol, *topic.related_symbols)],
            "points": [
                {"point_id": p.point_id, "text": p.text}
                for p in topic.expected_points
                if (topic.topic_id, p.point_id) in wanted
            ],
        }
        for topic in (analysis.topic(t) for t in topic_ids)
        if only_topic_id is None or topic.topic_id == only_topic_id
    ]
    topics = [t for t in topics if t["points"]]
    user = "\n\n".join(
        [
            "주제 자료입니다.",
            untrusted_block("topics", json.dumps(topics, ensure_ascii=False)),
        ]
    )
    return [
        {"role": "system", "content": QUIZ_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _same(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def validate_quiz(
    draft: QuizDraft, analysis: AnalysisResult, topic_ids: Sequence[str], seed: str
) -> list[QuizQuestion]:
    """쓸 수 있는 문제만 남기고 보기 순서를 정합니다.

    주제 자료에 없는 요점, 같은 요점의 두 번째 문제, 서로 다른 오답이 모자란 문제는 버립니다.
    보기 순서는 seed로 정해서 같은 면접이면 항상 같습니다.
    """
    order = quiz_points(analysis, topic_ids)
    kept: dict[tuple[str, str], QuizQuestion] = {}
    for item in draft.items:
        key = (item.topic_id.strip(), item.point_id.strip())
        question = item.question.strip()[:MAX_QUESTION_CHARS]
        correct = item.correct.strip()[:MAX_CHOICE_CHARS]
        if key not in order or key in kept or not question or not correct:
            continue
        if _same(correct, UNKNOWN_CHOICE):
            continue
        distractors: list[str] = []
        for raw in item.distractors:
            text = raw.strip()[:MAX_CHOICE_CHARS]
            taken = (correct, UNKNOWN_CHOICE, *distractors)
            if text and not any(_same(text, other) for other in taken):
                distractors.append(text)
        if len(distractors) < DISTRACTOR_COUNT:
            continue
        choices = [correct, *distractors[:DISTRACTOR_COUNT]]
        random.Random(f"{seed}:{key[0]}:{key[1]}").shuffle(choices)
        kept[key] = QuizQuestion(
            topic_id=key[0],
            point_id=key[1],
            question=question,
            choices=(*choices, UNKNOWN_CHOICE),
            correct_index=choices.index(correct),
        )
    return [kept[key] for key in order if key in kept]


def build_listener_messages(
    quiz: Sequence[QuizQuestion], transcripts: Sequence[str] | None
) -> list[Message]:
    """transcripts가 None이면 답변 기록 없이 푸는 호출(대조군)입니다. 규칙은 같습니다."""
    questions = [
        {
            "number": number,
            "question": q.question,
            "choices": [{"choice": i, "text": text} for i, text in enumerate(q.choices, start=1)],
        }
        for number, q in enumerate(quiz, start=1)
    ]
    if transcripts is None:
        parts = ["개발자의 설명 기록은 없습니다."]
    else:
        parts = [
            "개발자가 자기 코드를 설명한 기록입니다. 말한 순서대로입니다.",
            untrusted_block("explanation", json.dumps(list(transcripts), ensure_ascii=False)),
        ]
    parts += [
        "문제입니다.",
        untrusted_block("questions", json.dumps(questions, ensure_ascii=False)),
    ]
    return [
        {"role": "system", "content": LISTENER_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def picked_indexes(quiz: Sequence[QuizQuestion], answers: ListenerAnswers) -> list[int]:
    """청자가 고른 보기 번호(0부터)입니다. 답하지 않았거나 범위를 벗어난 답은 "알 수 없음"으로 봅니다."""
    picked = [q.unknown_index for q in quiz]
    answered: set[int] = set()
    for answer in answers.answers:
        at = answer.number - 1
        if not 0 <= at < len(quiz) or at in answered:
            continue
        answered.add(at)
        if 1 <= answer.choice <= len(quiz[at].choices):
            picked[at] = answer.choice - 1
    return picked


def grade(
    quiz: Sequence[QuizQuestion], blind: Sequence[int], informed: Sequence[int]
) -> ListenerTest:
    questions = [
        ListenerQuestion(
            topic_id=q.topic_id,
            point_id=q.point_id,
            question=q.question,
            choices=list(q.choices),
            correct_index=q.correct_index,
            blind_index=b,
            informed_index=i,
        )
        for q, b, i in zip(quiz, blind, informed, strict=True)
    ]
    return ListenerTest(
        questions=questions,
        blind_correct=sum(q.blind_index == q.correct_index for q in questions),
        informed_correct=sum(q.informed_index == q.correct_index for q in questions),
    )


async def generate_quiz(
    llm: LLMClient,
    analysis: AnalysisResult,
    topic_ids: Sequence[str],
    *,
    seed: str,
    timeout_s: float,
    model: str | None = None,
) -> list[QuizQuestion]:
    """주제마다 따로, 동시에 문제를 만듭니다. 실패한 주제의 문제는 빠집니다."""
    # 문제 수 상한 안에 요점이 하나라도 든 주제만 부릅니다.
    wanted = {topic_id for topic_id, _ in quiz_points(analysis, topic_ids)}

    async def one_topic(topic_id: str) -> QuizDraft | None:
        try:
            result = await llm.complete_structured(
                QuizDraft,
                build_quiz_messages(analysis, topic_ids, topic_id),
                purpose=QUIZ_PURPOSE,
                timeout_s=timeout_s,
                model=model,
            )
        except LLMError as error:
            logger.info("listener_quiz_topic_skipped reason=%s", type(error).__name__)
            return None
        return result.value

    drafts = await asyncio.gather(*(one_topic(t) for t in topic_ids if t in wanted))
    merged = QuizDraft(
        items=[item for draft in drafts if draft is not None for item in draft.items]
    )
    return validate_quiz(merged, analysis, topic_ids, seed)


async def solve_quiz(
    llm: LLMClient,
    quiz: Sequence[QuizQuestion],
    transcripts: Sequence[str] | None,
    *,
    timeout_s: float,
    model: str | None = None,
) -> list[int]:
    result = await llm.complete_structured(
        ListenerAnswers,
        build_listener_messages(quiz, transcripts),
        purpose=LISTENER_PURPOSE,
        timeout_s=timeout_s,
        model=model,
    )
    return picked_indexes(quiz, result.value)


async def run_listener_test(
    llm: LLMClient,
    analysis: AnalysisResult,
    topic_ids: Sequence[str],
    transcripts: Sequence[str],
    *,
    seed: str,
    timeout_s: float,
    quiz_model: str | None = None,
    listener_model: str | None = None,
) -> ListenerTest | None:
    """timeout_s는 문제 만들기와 청자 호출 두 번을 합친 전체 제한 시간입니다."""
    spoken = [t.strip() for t in transcripts if t.strip()]
    if not spoken or not topic_ids:
        return None
    try:
        async with asyncio.timeout(timeout_s):
            quiz = await generate_quiz(
                llm, analysis, topic_ids, seed=seed, timeout_s=timeout_s, model=quiz_model
            )
            if len(quiz) < MIN_QUESTIONS:
                logger.info("listener_test_skipped reason=too_few_questions count=%d", len(quiz))
                return None
            blind, informed = await asyncio.gather(
                solve_quiz(llm, quiz, None, timeout_s=timeout_s, model=listener_model),
                solve_quiz(llm, quiz, spoken, timeout_s=timeout_s, model=listener_model),
            )
    except (LLMError, TimeoutError) as error:
        logger.info("listener_test_skipped reason=%s", type(error).__name__)
        return None
    return grade(quiz, blind, informed)
