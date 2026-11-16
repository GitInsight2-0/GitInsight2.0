"""실제 LLM으로 샘플 레포 2단계 분석이 계약을 통과하는지 봅니다 (수동 실행, API 키 필요, 비용 발생).

수동 실행: uv run pytest -m "live_llm and network" worker/tests/test_stage2_live.py
내용 품질은 scripts/eval_stage2.py 기록으로 사람이 판단합니다.
"""

import pytest

from gitinsight_api.llm import LiteLLMClient
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_worker.git import git_cloner
from gitinsight_worker.pipeline import run_analysis
from gitinsight_worker.stage2 import LINE_NUMBER

from worker_test_support import Recorder

pytestmark = [pytest.mark.live_llm, pytest.mark.network]


async def test_sample_repo_topics_pass_contract_with_real_llm() -> None:
    sample = load_analysis()
    url = f"https://github.com/{sample.repo_full_name}.git"

    result = await run_analysis(
        git_cloner(url, sample.commit_sha),
        sample.commit_sha,
        Recorder(),
        LiteLLMClient(Settings().llm_analysis_model),
        analysis_id="live-stage2",
        repo_full_name=sample.repo_full_name,
    )

    assert len(result.topics) >= 3
    for topic in result.topics:
        assert topic.symbol.commit_sha == sample.commit_sha
        assert not LINE_NUMBER.search(topic.base_question)
