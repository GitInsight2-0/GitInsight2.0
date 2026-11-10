import ast
import urllib.request

import pytest

from gitinsight_contracts.analysis import ContributionLabel
from gitinsight_contracts.common import SymbolRef
from gitinsight_contracts.fixtures import load_analysis, load_conversations, show


def all_symbols() -> list[SymbolRef]:
    result = load_analysis()
    return [s for t in result.topics for s in (t.symbol, *t.related_symbols)]


def test_demo_analysis_has_six_topics_and_every_label() -> None:
    result = load_analysis()
    assert len(result.topics) == 6
    assert {t.contribution_label for t in result.topics} == set(ContributionLabel)


def test_symbol_ids_are_unique() -> None:
    ids = [s.symbol_id for s in all_symbols()]
    assert len(ids) == len(set(ids))


def test_point_ids_are_unique_across_topics() -> None:
    result = load_analysis()
    ids = [p.point_id for t in result.topics for p in t.expected_points]
    assert len(ids) == len(set(ids))


def test_symbol_id_matches_path_and_name() -> None:
    for symbol in all_symbols():
        path, qualified = symbol.symbol_id.split("::")
        assert path == symbol.path
        assert qualified.split(".")[-1] == symbol.name


def test_show_lists_every_topic_with_line_range() -> None:
    output = show()
    for topic in load_analysis().topics:
        assert f"{topic.symbol.path}:{topic.symbol.start_line}-{topic.symbol.end_line}" in output


def test_golden_conversations_reference_existing_points_and_topics() -> None:
    result = load_analysis()
    point_ids = {p.point_id for t in result.topics for p in t.expected_points}
    topic_ids = {t.topic_id for t in result.topics}
    for conversation in load_conversations():
        for turn in conversation.turns:
            if turn.expect_topic_id is not None:
                assert turn.expect_topic_id in topic_ids, conversation.name
            if turn.judgment is not None and turn.llm_behavior == "ok":
                for coverage in turn.judgment.coverage:
                    assert coverage.point_id in point_ids, conversation.name


def _find_range(source: str, qualified: str) -> tuple[int, int]:
    nodes: list[ast.stmt] = ast.parse(source).body
    found: ast.stmt | None = None
    for part in qualified.split("."):
        found = next(
            n
            for n in nodes
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and n.name == part
        )
        nodes = getattr(found, "body", [])
    assert found is not None
    return found.lineno, found.end_lineno or found.lineno


@pytest.mark.network
def test_line_ranges_match_pinned_commit() -> None:
    """고정 커밋의 실제 파일과 줄 범위를 대조합니다. 수동 실행: uv run pytest -m network"""
    result = load_analysis()
    cache: dict[str, str] = {}
    for symbol in all_symbols():
        if symbol.path not in cache:
            url = (
                f"https://raw.githubusercontent.com/{result.repo_full_name}/"
                f"{result.commit_sha}/{symbol.path}"
            )
            with urllib.request.urlopen(url, timeout=20) as response:
                cache[symbol.path] = response.read().decode("utf-8")
        qualified = symbol.symbol_id.split("::")[1]
        assert _find_range(cache[symbol.path], qualified) == (
            symbol.start_line,
            symbol.end_line,
        ), symbol.symbol_id
