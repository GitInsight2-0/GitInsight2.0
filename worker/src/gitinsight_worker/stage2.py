"""2단계 분석: 주제 후보마다 LLM으로 면접 재료를 만들고 서버 규칙으로 검증합니다.

- 어떤 심볼을 주제로 삼을지는 규칙(candidates.py)이 정합니다. LLM은 재료 문장만 씁니다.
- 코드 위치(SymbolRef), topic_id, point_id, 기여 라벨, 커밋은 서버가 붙입니다. LLM 출력에서 가져오지 않습니다.
- LLM이 고르는 ID는 관련 심볼뿐이고, 후보마다 정해 둔 허용 목록 안에 있어야 합니다.
- 코드와 README는 untrusted_block 안에만 넣고, 메모리에서만 쓰고 저장하지 않습니다.
- 검증에 실패하면 문제를 알려 주고 한 번 다시 요청합니다. 그래도 실패하면 그 후보를 버리고 다음 후보로 채웁니다.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import Field, ValidationError

from gitinsight_api.llm import LLMClient, LLMError, LLMResult, LLMUnavailableError, Message
from gitinsight_api.llm.untrusted import UNTRUSTED_NOTICE, untrusted_block
from gitinsight_contracts.analysis import (
    AnalysisResult,
    ContributionLabel,
    ExpectedPoint,
    PreparedFollowup,
    Topic,
)
from gitinsight_contracts.base import LLMOutput, count_sentences
from gitinsight_worker.candidates import Candidate, select_candidates
from gitinsight_worker.stage1 import Stage1Result

logger = logging.getLogger(__name__)

TOPIC_PURPOSE = "analysis_topic"
# 본 주제 5개와 여분 1개입니다 (면접 설정의 본 주제 수 5).
TARGET_TOPICS = 6
# 실패한 후보를 대신할 수 있게 목표의 3배까지만 후보로 봅니다. 코드도 이 후보만 읽습니다.
MAX_CANDIDATES = TARGET_TOPICS * 3
MIN_POINTS = 3
MAX_POINTS = 5
MAX_FOLLOWUPS = 3
MAX_RELATED = 3
# 면접관이 말하는 문장의 길이 상한입니다. 음성으로 듣고 한 번에 이해할 수 있어야 합니다.
MAX_SPOKEN_CHARS = 120
# 후보 순서대로 요청하는 목표 난이도입니다. 주제마다 따로 만들기 때문에 서버가 섞어 줍니다.
DIFFICULTY_CYCLE = (1, 2, 3)
# 주제 하나의 LLM 호출 제한 시간(초)입니다. 스키마 재시도를 포함합니다.
TOPIC_TIMEOUT_S = 60.0
# 제공사 사용량 제한(분당 요청 수)에 덜 걸리도록 동시에 2개까지만 요청합니다.
MAX_CONCURRENCY = 2
# 제공사 장애나 사용량 제한으로 실패하면 이 간격(초)으로 기다렸다 다시 시도합니다.
UNAVAILABLE_BACKOFF_S: tuple[float, ...] = (5.0, 20.0)
# 기다리면 풀릴 수 있는 제공사 오류(LiteLLM 예외 이름)입니다.
RETRYABLE_ERRORS = frozenset(
    {"RateLimitError", "ServiceUnavailableError", "InternalServerError", "APIConnectionError"}
)
# 키나 모델 이름이 잘못된 경우입니다. 기다리거나 다른 후보로 바꿔도 해결되지 않습니다.
CONFIG_ERRORS = frozenset({"AuthenticationError", "PermissionDeniedError", "NotFoundError"})
# 백오프를 다 쓰고도 사용량 제한이었던 후보가 이만큼 연속이면 분석을 멈춥니다.
QUOTA_BREAKER_LIMIT = 2
# 아주 긴 함수는 앞부분만 보냅니다. 비용과 지연을 제한합니다.
MAX_CODE_CHARS = 8_000
MAX_README_CHARS = 3_000

# 면접관이 줄 번호를 말하면 안 됩니다. 코드 위치는 분석 결과에서만 가져옵니다.
# 단독 "L12" 표기는 "L2 캐시", "L7 로드밸런서" 같은 기술 용어와 구분할 수 없어서 잡지 않습니다(PR #36 리뷰).
# 프롬프트가 줄 번호를 금지하므로 단독 표기는 드뭅니다. "행렬"도 줄 번호가 아닙니다.
LINE_NUMBER = re.compile(
    r"\d+\s*(번째\s*)?(줄|행(?!렬)|라인)"
    r"|(?<![A-Za-z])[Ll][Ii][Nn][Ee]\s*\d+"
)
_SLUG_INVALID = re.compile(r"[^a-z0-9]+")

TOPIC_SYSTEM_PROMPT = f"""당신은 한국어 기술 모의면접의 질문 재료를 만듭니다.
지원자 본인의 레포에 있는 코드 하나(target_code)를 보고, 그 코드에 대한 면접 주제 하나를 만드세요.

규칙:
- 모든 문장은 한국어로 씁니다. base_question, question_intent, hint, probe_question, prepared_followups의 question은 면접관이 소리 내어 그대로 말하는 문장이므로 존댓말 한 문장, {MAX_SPOKEN_CHARS}자 이하로 씁니다.
- base_question은 한 가지만 묻는 60자 안팎의 짧은 질문입니다. 코드의 동작이나 설명을 질문 안에 늘어놓지 않습니다.
- question_intent는 지원자가 질문 뜻을 되물을 때 면접관이 말하는 문장입니다. 무엇을 묻는지 쉬운 말로 풀어 주되, 답이 되는 내용(코드의 구체적인 동작, 조건, 값, 이름)은 쓰지 않습니다.
- 줄 번호나 "몇 번째 줄" 같은 위치 표현을 쓰지 않습니다. 코드는 함수, 클래스, 변수 이름으로 가리킵니다.
- summary는 이 코드가 실제로 하는 일을 3문장 이하로 설명합니다.
- expected_points는 좋은 답변이라면 다뤄야 할 요점 {MIN_POINTS}~{MAX_POINTS}개입니다. target_code에서 직접 확인할 수 있는 내용만 쓰고, 코드에 없는 내용을 지어내지 않습니다.
- 각 요점의 probe_question은 지원자가 그 요점을 빠뜨렸을 때 그대로 묻는 질문입니다. 요점의 답을 질문에 넣지 않습니다.
- prepared_followups는 1~{MAX_FOLLOWUPS}개의 꼬리질문입니다. 특정 관련 심볼을 보여 주며 물을 때만 symbol_id에 related_symbols의 symbol_id를 쓰고, 아니면 null로 둡니다.
- related_symbol_ids에는 related_symbols 중 이 주제와 관련 깊은 것을 최대 {MAX_RELATED}개 고릅니다. 목록에 없는 ID는 쓰지 않습니다.
- difficulty는 base_question이 묻는 수준입니다: 1(무엇을 어떤 순서로 하는지), 2(왜 이렇게 설계했는지, 다른 방법과 비교), 3(실패, 예외, 부하 상황에서 어떻게 되는지).
- 요청받은 난이도로 base_question을 만듭니다. 그 난이도를 물을 근거가 코드에 없으면 가장 가까운 난이도로 만들고, difficulty에는 실제로 만든 난이도를 씁니다.
- title은 20자 안팎의 짧은 한국어 제목입니다.
- hint는 답을 알려 주지 않고, 코드의 어느 부분을 보면 되는지 한 문장으로 알려 줍니다.

{UNTRUSTED_NOTICE}"""


class PointDraft(LLMOutput):
    text: str
    probe_question: str


class FollowupDraft(LLMOutput):
    question: str
    symbol_id: str | None


class TopicMaterial(LLMOutput):
    """주제 하나의 LLM 출력입니다. 워커 안에서만 쓰는 모델이라 계약(contracts)에 두지 않습니다."""

    title: str
    difficulty: Annotated[int, Field(ge=1, le=3)]
    summary: str
    base_question: str
    question_intent: str
    expected_points: list[PointDraft]
    prepared_followups: list[FollowupDraft]
    hint: str
    related_symbol_ids: list[str]


class TopicMaterialError(Exception):
    """검증 문제 목록입니다. LLM에 돌려줄 때 응답 본문은 넣지 않습니다."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class Stage2Error(Exception):
    """주제를 하나도 만들지 못했습니다."""


class Stage2AbortError(Stage2Error):
    """다른 후보로 바꿔도 해결되지 않아 분석을 바로 멈춥니다. 메시지에는 예외 종류 이름만 넣습니다."""


class LLMConfigError(Stage2AbortError):
    """LLM 키나 모델 설정이 잘못되었습니다."""


class LLMQuotaError(Stage2AbortError):
    """기다리고 다시 시도해도 사용량 제한이 풀리지 않습니다(하루 한도 소진 등)."""


class _QuotaBreaker:
    """백오프를 다 쓰고도 사용량 제한이었던 후보가 연속으로 나오면 분석을 멈춥니다.

    분당 한도는 기다리면 풀리지만, 하루 한도는 남은 후보를 다 시도해도 호출만 쓰고 몇 분 뒤 실패합니다.
    중간에 한 번이라도 응답을 받으면 다시 0부터 셉니다.
    """

    def __init__(self, limit: int = QUOTA_BREAKER_LIMIT) -> None:
        self.limit = limit
        self.consecutive = 0

    def succeeded(self) -> None:
        self.consecutive = 0

    def exhausted(self, kind: str) -> None:
        self.consecutive += 1
        if self.consecutive >= self.limit:
            raise LLMQuotaError(kind)


@dataclass(frozen=True)
class TopicContext:
    """서버가 정하는 주제 값입니다. LLM 출력에서 가져오지 않습니다."""

    topic_id: str
    contribution_label: ContributionLabel
    commits: list[str]


ContributionOf = Callable[[str], tuple[ContributionLabel, list[str]]]
ProgressFn = Callable[[float], Awaitable[None]]


def _no_contribution(_symbol_id: str) -> tuple[ContributionLabel, list[str]]:
    return ContributionLabel.UNVERIFIED, []


def topic_slug(symbol_name: str, used: set[str]) -> str:
    base = _SLUG_INVALID.sub("-", symbol_name.lower()).strip("-")[:90] or "topic"
    slug, n = base, 2
    while slug in used:
        slug, n = f"{base}-{n}", n + 1
    used.add(slug)
    return slug


# ---- 입력 준비 (동기, 파일 읽기) ----


def read_symbol_code(root: Path, candidate: Candidate) -> str:
    symbol = candidate.symbol
    text = (root / symbol.path).read_bytes().decode("utf-8", errors="replace")
    lines = text.splitlines()[symbol.start_line - 1 : symbol.end_line]
    code = "\n".join(lines)
    if len(code) > MAX_CODE_CHARS:
        code = code[:MAX_CODE_CHARS] + "\n# ... (길어서 이하 생략)"
    return code


def read_readme(root: Path) -> str:
    for path in sorted(root.iterdir()):
        if path.is_file() and not path.is_symlink() and path.stem.lower() == "readme":
            text = path.read_bytes()[: MAX_README_CHARS * 4].decode("utf-8", errors="replace")
            return text[:MAX_README_CHARS]
    return ""


# ---- 프롬프트 ----


def build_topic_messages(
    candidate: Candidate, code: str, readme: str, difficulty: int = 1
) -> list[Message]:
    symbol = candidate.symbol
    target = {"symbol_id": symbol.symbol_id, "name": symbol.name, "kind": symbol.kind.value}
    related = [
        {"symbol_id": s.symbol_id, "name": s.name, "kind": s.kind.value}
        for s in candidate.related_choices
    ]
    parts = [
        f"요청 난이도: {difficulty}",
        "주제로 삼을 코드의 정보입니다.",
        untrusted_block("target_symbol", json.dumps(target, ensure_ascii=False)),
        "주제로 삼을 코드입니다.",
        untrusted_block("target_code", code),
        "관련 심볼 목록입니다. related_symbol_ids와 symbol_id는 이 안에서만 고르세요.",
        untrusted_block("related_symbols", json.dumps(related, ensure_ascii=False)),
    ]
    if readme:
        parts += [
            "레포 README 앞부분입니다. 코드의 목적을 이해하는 데만 참고하세요.",
            untrusted_block("readme", readme),
        ]
    return [
        {"role": "system", "content": TOPIC_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


# ---- 서버 검증 ----


def _spoken_problems(field: str, text: str) -> list[str]:
    problems: list[str] = []
    if not text.strip():
        problems.append(f"{field}: 비어 있음")
    elif count_sentences(text) != 1:
        problems.append(f"{field}: 한 문장이어야 함")
    if len(text.strip()) > MAX_SPOKEN_CHARS:
        problems.append(f"{field}: {MAX_SPOKEN_CHARS}자 이하여야 함")
    return problems


def validate_material(material: TopicMaterial, candidate: Candidate, ctx: TopicContext) -> Topic:
    """LLM 출력을 Topic으로 바꿉니다. 문제가 있으면 TopicMaterialError를 올립니다."""
    problems: list[str] = []
    allowed = {s.symbol_id: s for s in candidate.related_choices}

    spoken = {
        "base_question": material.base_question,
        "question_intent": material.question_intent,
        "hint": material.hint,
    }
    points = _dedupe([p for p in material.expected_points if p.text.strip()], key=lambda p: p.text)
    for i, point in enumerate(points, 1):
        spoken[f"expected_points[{i}].probe_question"] = point.probe_question
    followups = material.prepared_followups[:MAX_FOLLOWUPS]
    for i, followup in enumerate(followups, 1):
        spoken[f"prepared_followups[{i}].question"] = followup.question
    for field, text in spoken.items():
        problems += _spoken_problems(field, text)

    every_text = {
        **spoken,
        "title": material.title,
        "summary": material.summary,
        **{f"expected_points[{i}].text": p.text for i, p in enumerate(points, 1)},
    }
    problems += [
        f"{f}: 줄 번호를 쓰면 안 됨" for f, t in every_text.items() if LINE_NUMBER.search(t)
    ]

    if not MIN_POINTS <= len(points) <= MAX_POINTS:
        problems.append(
            f"expected_points: {MIN_POINTS}~{MAX_POINTS}개여야 함 (지금 {len(points)}개)"
        )
    if not followups:
        problems.append("prepared_followups: 1개 이상이어야 함")
    for i, followup in enumerate(followups, 1):
        if followup.symbol_id is not None and followup.symbol_id not in allowed:
            problems.append(f"prepared_followups[{i}].symbol_id: 관련 심볼 목록에 없음")
    unknown = [s for s in material.related_symbol_ids if s not in allowed]
    if unknown:
        problems.append(f"related_symbol_ids: 관련 심볼 목록에 없는 ID {len(unknown)}개")
    if problems:
        raise TopicMaterialError(problems)

    related = [allowed[s] for s in _dedupe(material.related_symbol_ids, key=str)][:MAX_RELATED]
    # 꼬리질문이 가리키는 심볼은 하이라이트할 수 있게 관련 심볼에 넣습니다.
    for followup in followups:
        symbol = allowed.get(followup.symbol_id or "")
        if symbol is not None and symbol not in related:
            related.append(symbol)
    try:
        return Topic(
            topic_id=ctx.topic_id,
            title=material.title.strip(),
            symbol=candidate.symbol,
            related_symbols=related,
            contribution_label=ctx.contribution_label,
            difficulty=material.difficulty,
            summary=material.summary.strip(),
            base_question=material.base_question.strip(),
            question_intent=material.question_intent.strip(),
            expected_points=[
                ExpectedPoint(
                    point_id=f"p{i}",
                    text=p.text.strip(),
                    probe_question=p.probe_question.strip(),
                )
                for i, p in enumerate(points, 1)
            ],
            prepared_followups=[
                PreparedFollowup(question=f.question.strip(), symbol_id=f.symbol_id)
                for f in followups
            ],
            hint=material.hint.strip(),
            commits=ctx.commits,
        )
    except ValidationError as error:
        # 길이 제한 같은 계약 위반입니다. 입력값(응답 본문)은 넣지 않습니다.
        raise TopicMaterialError(
            [
                f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['type']}"
                for e in error.errors(include_input=False, include_url=False)[:10]
            ]
        ) from None


def _dedupe[T](items: list[T], *, key: Callable[[T], str]) -> list[T]:
    seen: set[str] = set()
    out: list[T] = []
    for item in items:
        k = key(item).strip()
        if k not in seen:
            seen.add(k)
            out.append(item)
    return out


def _feedback(problems: list[str]) -> str:
    return (
        "앞 응답이 규칙을 어겼습니다. 문제: "
        + "; ".join(problems[:10])
        + ". 규칙을 지켜 같은 형식의 JSON을 다시 출력하세요."
    )


# ---- 생성 ----


async def _complete_with_backoff(
    llm: LLMClient,
    messages: list[Message],
    *,
    timeout_s: float,
    model: str | None,
    breaker: _QuotaBreaker,
) -> LLMResult[TopicMaterial] | None:
    """제공사 장애·사용량 제한은 기다렸다 다시 시도합니다. 시간 초과와 거부는 다시 시도하지 않습니다.

    키나 모델 설정 오류는 다른 후보로도 해결되지 않으므로 LLMConfigError로 분석을 멈춥니다.
    다시 시도해도 사용량 제한이 계속되면 차단기가 LLMQuotaError로 분석을 멈춥니다.
    """
    for delay_s in (*UNAVAILABLE_BACKOFF_S, None):
        try:
            result = await llm.complete_structured(
                TopicMaterial, messages, purpose=TOPIC_PURPOSE, timeout_s=timeout_s, model=model
            )
        except LLMUnavailableError as error:
            # LLMClient는 제공사 예외의 종류 이름만 메시지로 넘깁니다.
            kind = str(error)
            logger.info("topic_llm_failed error=%s retry=%s", kind, delay_s)
            if kind in CONFIG_ERRORS:
                raise LLMConfigError(kind) from None
            if kind not in RETRYABLE_ERRORS:
                return None
            if delay_s is None:
                if kind == "RateLimitError":
                    breaker.exhausted(kind)
                return None
            await asyncio.sleep(delay_s)
            continue
        except LLMError as error:
            logger.info("topic_llm_failed error=%s", type(error).__name__)
            return None
        breaker.succeeded()
        return result
    return None


async def generate_topic(
    llm: LLMClient,
    candidate: Candidate,
    messages: list[Message],
    ctx: TopicContext,
    *,
    timeout_s: float = TOPIC_TIMEOUT_S,
    model: str | None = None,
    breaker: _QuotaBreaker | None = None,
) -> Topic | None:
    """주제 하나를 만듭니다. 검증 실패는 한 번 다시 요청하고, 그래도 실패하거나 LLM 오류면 None입니다."""
    breaker = breaker or _QuotaBreaker()
    for attempt in (1, 2):
        result = await _complete_with_backoff(
            llm, messages, timeout_s=timeout_s, model=model, breaker=breaker
        )
        if result is None:
            return None
        try:
            return validate_material(result.value, candidate, ctx)
        except TopicMaterialError as error:
            logger.info("topic_invalid attempt=%d problems=%d", attempt, len(error.problems))
            messages = [
                *messages,
                {"role": "assistant", "content": result.value.model_dump_json()},
                {"role": "user", "content": _feedback(error.problems)},
            ]
    return None


@dataclass(frozen=True)
class _Prepared:
    candidate: Candidate
    messages: list[Message]


def _prepare(root: Path, candidates: list[Candidate]) -> list[_Prepared]:
    readme = read_readme(root)
    prepared: list[_Prepared] = []
    for candidate in candidates:
        try:
            code = read_symbol_code(root, candidate)
        except OSError:
            continue
        difficulty = DIFFICULTY_CYCLE[len(prepared) % len(DIFFICULTY_CYCLE)]
        messages = build_topic_messages(candidate, code, readme, difficulty)
        prepared.append(_Prepared(candidate, messages))
    return prepared


async def generate_topics(
    root: Path,
    stage1: Stage1Result,
    llm: LLMClient,
    *,
    contribution_of: ContributionOf | None = None,
    target: int = TARGET_TOPICS,
    timeout_s: float = TOPIC_TIMEOUT_S,
    model: str | None = None,
    progress: ProgressFn | None = None,
) -> list[Topic]:
    """후보 순서대로 주제를 만듭니다. 실패한 후보는 다음 후보로 채워서 최대 `target`개를 돌려줍니다."""
    # 기여 근거를 넘기지 않으면 모두 "확인 불가"로 둡니다.
    contribution = contribution_of or _no_contribution
    candidates = select_candidates(stage1.symbols, lambda s: contribution(s.symbol_id)[0])
    prepared = await asyncio.to_thread(_prepare, root, candidates[:MAX_CANDIDATES])
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    used_slugs: set[str] = set()
    breaker = _QuotaBreaker()
    topics: list[Topic] = []
    next_index = 0

    async def one(item: _Prepared) -> Topic | None:
        label, commits = contribution(item.candidate.symbol.symbol_id)
        ctx = TopicContext(topic_slug(item.candidate.symbol.name, used_slugs), label, commits)
        async with semaphore:
            return await generate_topic(
                llm,
                item.candidate,
                item.messages,
                ctx,
                timeout_s=timeout_s,
                model=model,
                breaker=breaker,
            )

    # 부족한 만큼만 다음 후보를 한 번에 요청합니다. 결과 순서는 후보 순서를 따릅니다.
    while len(topics) < target and next_index < len(prepared):
        wave = prepared[next_index : next_index + target - len(topics)]
        next_index += len(wave)
        # 즉시 중단 오류(설정 오류, 한도 소진)가 나면 TaskGroup이 같은 묶음의 다른 요청을 취소합니다.
        try:
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(one(item)) for item in wave]
        except* Stage2AbortError as errors:
            raise errors.exceptions[0] from None
        topics += [t for t in (task.result() for task in tasks) if t is not None]
        if progress is not None:
            await progress(min(len(topics) / target, 1.0))
    logger.info("stage2_topics made=%d tried=%d", len(topics), next_index)
    return topics


async def run_stage2(
    root: Path,
    stage1: Stage1Result,
    llm: LLMClient,
    *,
    analysis_id: str,
    repo_full_name: str,
    contribution_of: ContributionOf | None = None,
    timeout_s: float = TOPIC_TIMEOUT_S,
    model: str | None = None,
    progress: ProgressFn | None = None,
) -> AnalysisResult:
    """1단계 결과와 내려받은 레포(root)로 AnalysisResult를 만듭니다. 주제가 하나도 없으면 Stage2Error입니다."""
    topics = await generate_topics(
        root,
        stage1,
        llm,
        contribution_of=contribution_of,
        timeout_s=timeout_s,
        model=model,
        progress=progress,
    )
    if not topics:
        raise Stage2Error("면접 주제를 하나도 만들지 못했습니다")
    return AnalysisResult(
        analysis_id=analysis_id,
        repo_full_name=repo_full_name,
        commit_sha=stage1.commit_sha,
        languages=stage1.languages,
        symbol_count=stage1.symbol_count,
        topics=topics,
    )
