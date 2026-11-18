"""TypeScript 샘플 레포에서 워커가 뽑은 심볼의 줄 범위를 고정 커밋에서 대조합니다 (#72).

기대값은 같은 커밋을 TypeScript 컴파일러(`ts.createSourceFile`)로 읽어 구한 줄 범위입니다.
워커(tree-sitter)와 다른 도구로 구한 값이라, 워커의 규칙이 바뀌어 줄이 어긋나면 여기서 드러납니다.
레포 주인이 동의한 공개 레포이고, 원본 코드는 저장소에 넣지 않습니다.

수동 실행: uv run pytest -m network worker/tests/test_script_sample_match.py
"""

from pathlib import Path

import pytest

from gitinsight_contracts.common import SymbolKind
from gitinsight_worker.candidates import select_candidates
from gitinsight_worker.git import git_cloner
from gitinsight_worker.stage1 import Stage1Result, analyze_tree

pytestmark = pytest.mark.network

REPO = "HYUNNNNNNNN/underlayer"
COMMIT = "a0f0c277769b08c6d46f64b15a6b761a5249826b"

# symbol_id별 (시작 줄, 끝 줄)입니다. 이 커밋의 정의는 모두 최상위 함수입니다.
EXPECTED: dict[str, tuple[int, int]] = {
    "app/api/layers/[id]/comments/route.ts::GET": (23, 37),
    "app/api/layers/[id]/comments/route.ts::POST": (40, 78),
    "app/api/layers/[id]/respect/route.ts::PATCH": (23, 47),
    "app/api/layers/[id]/route.ts::DELETE": (22, 41),
    "app/api/layers/route.ts::GET": (23, 33),
    "app/api/layers/route.ts::POST": (36, 58),
    "app/api/profile/route.ts::GET": (24, 36),
    "app/api/profile/route.ts::POST": (39, 69),
    "app/api/profile/route.ts::PATCH": (72, 100),
    "app/components/AuthButton.tsx::AuthButton": (5, 35),
    "app/components/AuthProvider.tsx::AuthProvider": (5, 7),
    "app/components/LayerDetailModal.tsx::LayerDetailModal": (36, 224),
    "app/components/MyCanvas.tsx::MyCanvas": (22, 290),
    "app/components/MyProfile.tsx::MyProfile": (15, 363),
    "app/components/Providers.tsx::MyProfile": (7, 111),
    "app/components/QuickSketch.tsx::QuickSketch": (14, 192),
    "app/components/StudioFeed.tsx::StudioFeed": (28, 265),
    "app/components/UserCanvas.tsx::UserCanvas": (20, 138),
    "app/layout.tsx::RootLayout": (17, 46),
    "app/page.tsx::Home": (12, 100),
}


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("clone") / "repo"
    git_cloner(f"https://github.com/{REPO}.git", COMMIT)(path)
    return path


@pytest.fixture(scope="module")
def result(repo: Path) -> Stage1Result:
    return analyze_tree(repo, COMMIT)


def test_every_symbol_has_the_same_range_as_the_typescript_compiler(result: Stage1Result) -> None:
    extracted = {s.symbol_id: (s.start_line, s.end_line) for s in result.symbols}

    assert extracted == EXPECTED
    assert {s.kind for s in result.symbols} == {SymbolKind.FUNCTION}
    assert {s.commit_sha for s in result.symbols} == {COMMIT}


def test_ranges_point_at_the_definition_in_the_real_code(repo: Path, result: Stage1Result) -> None:
    for symbol in result.symbols:
        lines = (repo / symbol.path).read_text(encoding="utf-8").splitlines()
        first, last = lines[symbol.start_line - 1], lines[symbol.end_line - 1]
        assert f"function {symbol.name}(" in first, symbol.symbol_id
        assert last.strip() == "}", symbol.symbol_id


def test_symbol_count_and_languages(result: Stage1Result) -> None:
    # 내보낸 20개와 함수 안에 중첩된 정의 20개입니다.
    assert result.symbol_count == 40
    assert [(s.language, s.file_count) for s in result.languages] == [
        ("TypeScript", 19),
        ("Markdown", 5),
        ("JSON", 3),
        ("JavaScript", 2),
        ("CSS", 1),
    ]


def test_blame_and_candidate_selection_work_on_script_symbols(result: Stage1Result) -> None:
    # 기여 근거가 모든 심볼에 있고, 최근 커밋을 실제 blame에서 읽어 옵니다.
    assert set(result.contributions) == set(EXPECTED)
    assert all(c.commits for c in result.contributions.values())
    # 참조는 Python 파일에서만 세므로 알 수 없음(없는 키)입니다.
    assert result.references == {}
    candidates = select_candidates(result.symbols)
    assert candidates[0].symbol.symbol_id == "app/components/MyProfile.tsx::MyProfile"
    assert len(candidates) == 18
