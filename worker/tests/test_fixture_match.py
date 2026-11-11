"""샘플 데이터(fixtures/analysis)를 워커가 같은 값으로 만들어 내는지 고정 커밋에서 대조합니다.

수동 실행: uv run pytest -m network worker/tests/test_fixture_match.py
"""

import asyncio

import pytest

from gitinsight_contracts.fixtures import load_analysis
from gitinsight_worker.contribution import Requester
from gitinsight_worker.git import git_cloner
from gitinsight_worker.stage1 import Stage1Result, run_stage1

from worker_test_support import Recorder

pytestmark = pytest.mark.network

# 샘플 데이터의 기여 라벨은 레포 주인이 분석을 요청했다고 보고 정했습니다.
REQUESTER = Requester.of(names=["tttae210-sys"])


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> Stage1Result:
    analysis = load_analysis()
    url = f"https://github.com/{analysis.repo_full_name}.git"
    return asyncio.run(
        run_stage1(
            git_cloner(url, analysis.commit_sha),
            analysis.commit_sha,
            Recorder(),
            requester=REQUESTER,
            work_dir=tmp_path_factory.mktemp("clone"),
        )
    )


def test_every_sample_symbol_is_extracted_with_same_range(result: Stage1Result) -> None:
    extracted = {s.symbol_id: s for s in result.symbols}
    analysis = load_analysis()
    for topic in analysis.topics:
        for expected in (topic.symbol, *topic.related_symbols):
            assert extracted.get(expected.symbol_id) == expected, expected.symbol_id


def test_symbol_count_and_languages_match_sample(result: Stage1Result) -> None:
    analysis = load_analysis()

    assert result.symbol_count == analysis.symbol_count
    assert {(s.language, s.file_count) for s in result.languages} == {
        (s.language, s.file_count) for s in analysis.languages
    }


def test_contribution_label_and_commits_match_sample(result: Stage1Result) -> None:
    analysis = load_analysis()

    for topic in analysis.topics:
        contribution = result.contributions[topic.symbol.symbol_id]
        assert contribution.label is topic.contribution_label, topic.topic_id
        assert list(contribution.commits) == topic.commits, topic.topic_id
    # 샘플에는 세 라벨이 모두 있습니다.
    assert len({t.contribution_label for t in analysis.topics}) == 3
