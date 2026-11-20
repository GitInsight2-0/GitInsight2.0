"""분석 엔진(A)이 만들고 면접 두뇌(B)와 화면(C)이 읽는 분석 결과 계약."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from gitinsight_contracts.base import Contract, count_sentences
from gitinsight_contracts.common import CommitSha, LineSpan, SymbolId, SymbolRef

Id = Annotated[str, Field(min_length=1, max_length=100)]
# 면접관이 그대로 읽는 문장입니다. 한 문장으로 제한해야 발화문 2문장 제한을 지킬 수 있습니다.
OneSentence = Annotated[str, Field(min_length=1, max_length=200)]
# 다음 질문 앞에 붙여 한 번에 말하는 문장입니다.
# 질문(200자)과 합쳐 발화문 400자 제한에 들어가도록 더 짧게 둡니다.
ShortSentence = Annotated[str, Field(min_length=1, max_length=150)]


class ContributionLabel(StrEnum):
    """git 이력으로 본 기여 정도입니다. 질문 순서와 표현에만 쓰고 점수에는 쓰지 않습니다."""

    VERIFIED = "verified"
    SHARED = "shared"
    UNVERIFIED = "unverified"


CONTRIBUTION_LABEL_TEXT: dict[ContributionLabel, str] = {
    ContributionLabel.VERIFIED: "기여 근거 확인",
    ContributionLabel.SHARED: "공동 기여",
    ContributionLabel.UNVERIFIED: "확인 불가",
}


def _require_one_sentence(value: str, field: str) -> None:
    if count_sentences(value) != 1:
        raise ValueError(f"{field}는 한 문장이어야 합니다: {value!r}")


class ExpectedPoint(Contract):
    """좋은 답변이라면 다뤄야 할 요점입니다."""

    point_id: Id
    text: Annotated[str, Field(min_length=1, max_length=300)]
    # 이 요점이 빠졌을 때 PROBE 행동에서 그대로 묻는 질문입니다.
    probe_question: OneSentence
    # 주제 코드에서 이 요점과 관계있는 줄입니다 (#156). 주제 심볼의 줄 범위 안에 있습니다.
    # 고르지 못했거나 이 필드가 생기기 전의 분석 결과면 null이고, 그때는 주제 심볼 전체를 봅니다.
    focus: LineSpan | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        _require_one_sentence(self.probe_question, "probe_question")
        return self


class PreparedFollowup(Contract):
    """LLM 없이도 쓸 수 있게 미리 만든 꼬리질문입니다."""

    question: OneSentence
    # 이 꼬리질문이 가리키는 관련 심볼입니다. 심볼 id와 같은 길이까지 받습니다 (#183).
    symbol_id: SymbolId | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        _require_one_sentence(self.question, "question")
        return self


class MisstatementKind(StrEnum):
    """틀린 말을 만든 방식입니다."""

    # 주제 코드가 직접 부르지 않는 함수를 부른다고 말합니다.
    ABSENT_CALL = "absent_call"
    # 기대 답변 요점 하나를 반대로 말합니다.
    INVERTED_POINT = "inverted_point"


class Misstatement(Contract):
    """면접관이 일부러 하는 틀린 말입니다 (#133).

    2단계 분석에서 만들고 서버가 검증합니다. 면접 중에 지어내지 않습니다.
    """

    kind: MisstatementKind
    # 면접관이 그대로 읽는 틀린 말입니다.
    statement: ShortSentence
    # 면접자가 짚지 못했을 때 면접관이 그대로 읽는 정답입니다. 리포트에도 보여 줍니다.
    correction: ShortSentence
    # 반대로 말한 요점입니다. inverted_point일 때만 있습니다.
    point_id: Id | None = None
    # 부른다고 말한 함수입니다. absent_call일 때만 있습니다.
    # 함수 이름과 위치는 LLM 문장이 아니라 분석 결과에서 가져옵니다.
    # 주제 코드가 이 함수를 직접 부르지 않는다는 것은 2단계 분석이 확인합니다.
    absent_symbol: SymbolRef | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        for field in ("statement", "correction"):
            _require_one_sentence(getattr(self, field), field)
        inverted = self.kind is MisstatementKind.INVERTED_POINT
        if inverted != (self.point_id is not None):
            raise ValueError("point_id는 inverted_point일 때만, 그리고 반드시 있어야 합니다")
        absent = self.kind is MisstatementKind.ABSENT_CALL
        if absent != (self.absent_symbol is not None):
            raise ValueError("absent_symbol은 absent_call일 때만, 그리고 반드시 있어야 합니다")
        if self.absent_symbol is not None and self.absent_symbol.name not in self.statement:
            raise ValueError("statement에 absent_symbol의 이름이 들어 있어야 합니다")
        return self


class Topic(Contract):
    """면접 주제 하나입니다. 2단계 분석에서 만들어집니다."""

    topic_id: Id
    title: Annotated[str, Field(min_length=1, max_length=100)]
    symbol: SymbolRef
    # 꼬리질문 하이라이트에 쓸 수 있는 심볼 목록입니다. 주 심볼은 넣지 않아도 허용됩니다.
    related_symbols: list[SymbolRef] = Field(default_factory=list)
    contribution_label: ContributionLabel
    # 1: 표면 이해, 2: 설계 의도, 3: 장애 대응. 쉬운 주제부터 묻는 데 씁니다.
    difficulty: Annotated[int, Field(ge=1, le=3)]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    base_question: OneSentence
    # CLARIFY 행동에서 질문 취지를 설명하는 한 문장입니다.
    question_intent: OneSentence
    expected_points: Annotated[list[ExpectedPoint], Field(min_length=3, max_length=5)]
    prepared_followups: Annotated[list[PreparedFollowup], Field(min_length=1, max_length=3)]
    hint: OneSentence
    # 이 범위를 수정한 최근 커밋 SHA입니다.
    commits: list[CommitSha] = Field(default_factory=list)
    # 이 주제에서 면접관이 일부러 할 수 있는 틀린 말입니다 (#133).
    # 검증을 통과한 것이 없거나 이 필드가 생기기 전의 분석 결과면 null입니다.
    misstatement: Misstatement | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        for field in ("base_question", "question_intent", "hint"):
            _require_one_sentence(getattr(self, field), field)
        point_ids = [p.point_id for p in self.expected_points]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError(f"{self.topic_id}: point_id가 중복됩니다")
        for point in self.expected_points:
            if point.focus is not None and not point.focus.within(self.symbol):
                raise ValueError(
                    f"{self.topic_id}: {point.point_id}의 focus가 주제 코드 범위 밖입니다"
                )
        inverted = self.misstatement.point_id if self.misstatement is not None else None
        if inverted is not None and inverted not in point_ids:
            raise ValueError(f"{self.topic_id}: 틀린 말의 point_id {inverted}가 요점에 없습니다")
        absent = self.misstatement.absent_symbol if self.misstatement is not None else None
        if absent is not None and absent.symbol_id == self.symbol.symbol_id:
            raise ValueError(f"{self.topic_id}: 틀린 말의 absent_symbol이 주제 코드 자신입니다")
        allowed = self.allowed_symbol_ids()
        for followup in self.prepared_followups:
            if followup.symbol_id is not None and followup.symbol_id not in allowed:
                raise ValueError(f"{self.topic_id}: 허용 목록 밖 symbol_id {followup.symbol_id}")
        return self

    def allowed_symbol_ids(self) -> set[str]:
        return {self.symbol.symbol_id, *(s.symbol_id for s in self.related_symbols)}

    def find_symbol(self, symbol_id: str) -> SymbolRef | None:
        for symbol in (self.symbol, *self.related_symbols):
            if symbol.symbol_id == symbol_id:
                return symbol
        return None


class LanguageStat(Contract):
    language: Annotated[str, Field(min_length=1, max_length=50)]
    file_count: Annotated[int, Field(ge=0)]


class AnalysisResult(Contract):
    """면접에 필요한 분석 결과 전체입니다. 원본 코드는 들어가지 않습니다."""

    analysis_id: Id
    repo_full_name: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
    commit_sha: CommitSha
    languages: list[LanguageStat]
    symbol_count: Annotated[int, Field(ge=0)]
    topics: Annotated[list[Topic], Field(min_length=1)]

    @model_validator(mode="after")
    def _check(self) -> Self:
        topic_ids = [t.topic_id for t in self.topics]
        if len(topic_ids) != len(set(topic_ids)):
            raise ValueError("topic_id가 중복됩니다")
        for topic in self.topics:
            for symbol in (topic.symbol, *topic.related_symbols):
                if symbol.commit_sha != self.commit_sha:
                    raise ValueError(f"{symbol.symbol_id}: 분석 커밋과 다른 commit_sha")
        return self

    def topic(self, topic_id: str) -> Topic:
        for topic in self.topics:
            if topic.topic_id == topic_id:
                return topic
        raise KeyError(topic_id)


class TopicPreview(Contract):
    topic_id: Id
    title: str
    symbol: SymbolRef
    contribution_label: ContributionLabel


class AnalysisSummary(Contract):
    """GET /api/analyses/{id}/summary 응답입니다."""

    languages: list[LanguageStat]
    symbol_count: int
    topics: list[TopicPreview]

    @classmethod
    def from_result(cls, result: AnalysisResult) -> "AnalysisSummary":
        return cls(
            languages=result.languages,
            symbol_count=result.symbol_count,
            topics=[
                TopicPreview(
                    topic_id=t.topic_id,
                    title=t.title,
                    symbol=t.symbol,
                    contribution_label=t.contribution_label,
                )
                for t in result.topics
            ],
        )
