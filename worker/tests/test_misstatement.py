"""면접관이 일부러 하는 틀린 말 재료 고르기와 검증 (#133, #142)."""

import json
import re
import textwrap
from pathlib import Path

import pytest

from gitinsight_api.llm import Message
from gitinsight_api.llm.fake import FakeLLM, FakeStep
from gitinsight_contracts.analysis import ContributionLabel, MisstatementKind
from gitinsight_worker.candidates import select_candidates
from gitinsight_worker.misstatement import MisstatementPlanner
from gitinsight_worker.stage1 import analyze_tree
from gitinsight_worker.stage2 import (
    TOPIC_PURPOSE,
    TopicContext,
    TopicMaterialError,
    attach_misstatement,
    generate_topics,
    validate_material,
)

from worker_test_support import REPO_FILES, absent_draft, inverted_draft, material, write_repo

SHA = "d" * 40
CTX = TopicContext("handle", ContributionLabel.SHARED, ["e" * 40])


def _body(name: str) -> str:
    """후보가 될 만큼 긴 함수 본문입니다."""
    lines = "".join(f"    v{i} = {i}\n" for i in range(8))
    return f"def {name}():\n{lines}    return v0\n"


def plan_for(root: Path, files: dict[str, str], target_id: str):
    write_repo(root, files)
    stage1 = analyze_tree(root, SHA)
    target = next(s for s in stage1.symbols if s.symbol_id == target_id)
    return MisstatementPlanner(root, stage1.symbols).plan(target)


# ---- 부르지 않는 함수 고르기 ----


def test_picks_function_whose_name_never_appears_in_topic_file(tmp_path: Path) -> None:
    files = {
        "app/handler.py": "from app.util import helper\n\n\n"
        + _body("handle").replace("v0 = 0", "v0 = helper()"),
        "app/util.py": _body("helper"),
        "app/store.py": _body("save_report"),
    }

    plan = plan_for(tmp_path, files, "app/handler.py::handle")

    # helper는 실제로 부르므로 고르지 않습니다.
    assert plan.kind is MisstatementKind.ABSENT_CALL
    assert plan.absent_symbol is not None
    assert plan.absent_symbol.symbol_id == "app/store.py::save_report"


@pytest.mark.parametrize(
    "mention",
    [
        # 별칭 import: 주제 코드에는 h만 나오지만 원래 이름이 import 문에 남습니다.
        "from app.store import save_report as h\n",
        # 문자열로 부르는 동적 호출
        'import app.store as store\nfn = getattr(store, "save_report")\n',
        # 주제 범위 밖에서 붙인 별칭
        "from app import store\nsave = store.save_report\n",
    ],
)
def test_name_anywhere_in_topic_file_excludes_function(tmp_path: Path, mention: str) -> None:
    files = {
        "app/handler.py": mention + "\n\n" + _body("handle"),
        "app/store.py": _body("save_report"),
    }

    plan = plan_for(tmp_path, files, "app/handler.py::handle")

    assert plan.kind is MisstatementKind.INVERTED_POINT
    assert plan.absent_symbol is None


def test_unlikely_functions_are_not_chosen(tmp_path: Path) -> None:
    files = {
        "app/handler.py": _body("handle"),
        # 같은 이름이 두 곳에 있으면 어느 것인지 모릅니다.
        "app/a.py": _body("load_items"),
        "app/b.py": _body("load_items"),
        # 테스트, 지금 쓰지 않는 경로, 비공개, 너무 짧은 이름
        "tests/test_store.py": _body("save_fixture"),
        "app/legacy/old.py": _body("save_legacy"),
        "app/private.py": _body("_save_private"),
        "app/short.py": _body("run"),
        # 다른 언어
        "web/lib/api.ts": "export function fetchItems() {\n"
        + "  const a = 1;\n" * 8
        + "  return a;\n}\n",
    }

    plan = plan_for(tmp_path, files, "app/handler.py::handle")

    assert plan.kind is MisstatementKind.INVERTED_POINT


def test_closest_folder_is_chosen_first(tmp_path: Path) -> None:
    files = {
        "app/api/handler.py": _body("handle"),
        "other/far.py": _body("far_away"),
        "app/near.py": _body("near_by"),
        "app/api/sibling.py": _body("same_folder"),
    }

    plan = plan_for(tmp_path, files, "app/api/handler.py::handle")

    assert plan.absent_symbol is not None
    assert plan.absent_symbol.name == "same_folder"


def test_imported_module_comes_first_and_unrelated_area_is_skipped(tmp_path: Path) -> None:
    # handler.py는 app/store.py의 다른 함수를 가져다 씁니다. 그 모듈의 안 쓰는 함수가 가장 그럴듯합니다.
    handler = "from app.store import load_items\n\n\n" + _body("handle").replace(
        "v0 = 0", "v0 = load_items()"
    )
    files = {
        "app/api/handler.py": handler,
        "app/store.py": _body("load_items") + "\n\n" + _body("save_items"),
        "app/api/sibling.py": _body("same_folder"),
    }

    plan = plan_for(tmp_path, files, "app/api/handler.py::handle")

    assert plan.absent_symbol is not None
    assert plan.absent_symbol.name == "save_items"

    # 레포의 다른 영역(최상위 폴더가 다르고 import도 안 함)의 함수만 있으면 고르지 않습니다.
    lonely = plan_for(
        tmp_path / "other",
        {"frontend/ui.py": _body("render"), "app/agents/builder.py": _body("build_repo")},
        "frontend/ui.py::render",
    )
    assert lonely.kind is MisstatementKind.INVERTED_POINT


def test_longer_function_is_chosen_within_same_rank(tmp_path: Path) -> None:
    longer = _body("long_helper").replace(
        "    return v0\n", "    v9 = 9\n    v10 = 10\n    return v0\n"
    )
    files = {
        "app/handler.py": _body("handle"),
        "app/a_short.py": _body("short_helper"),
        "app/b_long.py": longer,
    }

    plan = plan_for(tmp_path, files, "app/handler.py::handle")

    assert plan.absent_symbol is not None
    assert plan.absent_symbol.name == "long_helper"


def _ts(name: str, prefix: str = "export function") -> str:
    return f"{prefix} {name}() {{\n" + "  const a = 1;\n" * 8 + "  return a;\n}\n"


def test_script_alias_default_exports_and_components_are_excluded(tmp_path: Path) -> None:
    files = {
        "web/lib/session.ts": textwrap.dedent(
            """\
            import { postTurn as send } from "./turns";
            import makeToken from "./token";
            import legacyMetric from "./metric";

            export async function startSession() {
              const a = await send();
              const b = makeToken();
              const c = legacyMetric();
              const d = a + b + c;
              const e = d * 2;
              const f = e - 1;
              const g = f + 3;
              return g;
            }
            """
        ),
        "web/lib/turns.ts": _ts("postTurn"),
        "web/lib/token.ts": _ts("fetchVoiceToken", "export default function"),
        "web/lib/metric.ts": _ts("postMetric", "function") + "module.exports = postMetric;\n",
        # 화면 컴포넌트는 부르는 것이 아니라 그리는 것입니다.
        "web/components/CodeViewer.tsx": _ts("CodeViewer"),
        "web/lib/report.ts": _ts("loadReport"),
    }

    plan = plan_for(tmp_path, files, "web/lib/session.ts::startSession")

    assert plan.absent_symbol is not None
    assert plan.absent_symbol.symbol_id == "web/lib/report.ts::loadReport"


def test_same_input_gives_same_choice(tmp_path: Path) -> None:
    files = {
        "app/handler.py": _body("handle"),
        "app/b_store.py": _body("save_b"),
        "app/a_store.py": _body("save_a"),
    }

    first = plan_for(tmp_path, files, "app/handler.py::handle")
    second = plan_for(tmp_path, files, "app/handler.py::handle")

    # 같은 순위면 경로 순서입니다.
    assert first == second
    assert first.absent_symbol is not None
    assert first.absent_symbol.name == "save_a"


# ---- 검증 ----


def handle_topic(root: Path):
    write_repo(root)
    stage1 = analyze_tree(root, SHA)
    candidate = next(c for c in select_candidates(stage1.symbols) if c.symbol.name == "handle")
    plan = MisstatementPlanner(root, stage1.symbols).plan(candidate.symbol)
    return candidate, plan


def test_absent_call_is_attached_with_server_symbol(tmp_path: Path) -> None:
    candidate, plan = handle_topic(tmp_path)
    assert plan.absent_symbol is not None
    name = plan.absent_symbol.name
    draft = absent_draft(name).model_copy(
        update={"statement": f"여기서 직접 `{name}`를 호출해서 값을 준비하죠?"}
    )
    mat = material(misstatement=draft)

    topic = attach_misstatement(mat, validate_material(mat, candidate, CTX), plan)

    assert topic.misstatement is not None
    assert topic.misstatement.kind is MisstatementKind.ABSENT_CALL
    # 위치는 LLM이 아니라 서버가 고른 SymbolRef입니다.
    assert topic.misstatement.absent_symbol == plan.absent_symbol
    assert topic.misstatement.point_id is None
    # 소리 내어 읽는 문장이라 백틱은 뺍니다.
    assert "`" not in topic.misstatement.statement


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            {"correction": "방금 말씀드린 것과 달리 이 코드는 그 함수를 직접 호출하지 않습니다."},
            "correction: absent_function의 name이 없음",
        ),
        (
            {"statement": "제가 일부러 틀리게 말하면, 여기서 직접 {name}를 호출하죠?"},
            "틀린 말이라는 것을 드러내면 안 됨",
        ),
        ({"point_index": 1}, "absent_call이면 null이어야 함"),
        (
            {"statement": "여기서 직접 {name}를 호출하죠. 그리고 결과를 저장하죠?"},
            "한 문장이어야 함",
        ),
        ({"statement": "여기서 직접 {name}를 " + "아주 " * 50 + "호출하죠?"}, "120자 이하여야 함"),
        ({"statement": "L12에서 직접 {name}를 호출하죠?"}, "줄 번호를 쓰면 안 됨"),
    ],
)
def test_invalid_absent_call_is_rejected(
    tmp_path: Path, change: dict[str, object], problem: str
) -> None:
    candidate, plan = handle_topic(tmp_path)
    assert plan.absent_symbol is not None
    name = plan.absent_symbol.name
    update = {k: v.format(name=name) if isinstance(v, str) else v for k, v in change.items()}
    mat = material(misstatement=absent_draft(name).model_copy(update=update))
    topic = validate_material(mat, candidate, CTX)

    with pytest.raises(TopicMaterialError) as info:
        attach_misstatement(mat, topic, plan)

    assert any(problem in p for p in info.value.problems)


def test_inverted_point_maps_index_to_server_point_id(tmp_path: Path) -> None:
    write_repo(tmp_path, {"app/handler.py": REPO_FILES["app/handler.py"]})
    stage1 = analyze_tree(tmp_path, SHA)
    [candidate] = select_candidates(stage1.symbols)
    plan = MisstatementPlanner(tmp_path, stage1.symbols).plan(candidate.symbol)
    assert plan.kind is MisstatementKind.INVERTED_POINT

    mat = material(misstatement=inverted_draft(point_index=2))
    topic = attach_misstatement(mat, validate_material(mat, candidate, CTX), plan)

    assert topic.misstatement is not None
    assert topic.misstatement.point_id == "p2"
    assert topic.misstatement.absent_symbol is None

    for bad in (None, 0, 4):
        bad_mat = material(misstatement=inverted_draft(point_index=bad))
        with pytest.raises(TopicMaterialError, match="expected_points 안의 번호"):
            attach_misstatement(bad_mat, validate_material(bad_mat, candidate, CTX), plan)


# ---- 생성 흐름 ----

_UNTRUSTED = re.compile(r"<untrusted_data [^>]*>.*?</untrusted_data>", re.S)
_ABSENT = re.compile(r'<untrusted_data kind="absent_function">\n(.*?)\n</untrusted_data>', re.S)


def _user(messages: list[Message]) -> str:
    return "\n".join(m["content"] for m in messages if m["role"] == "user")


def answer_with_misstatement(_purpose: str, _schema: object, messages: list[Message]) -> FakeStep:
    """프롬프트의 absent_function 이름으로 틀린 말을 씁니다. 없으면 요점을 반대로 말합니다."""
    found = _ABSENT.search(_user(messages))
    if found is None:
        return FakeStep(material())
    name = json.loads(found.group(1))["name"]
    return FakeStep(material(misstatement=absent_draft(name)))


async def test_topics_get_misstatements_and_names_stay_in_untrusted_block(tmp_path: Path) -> None:
    write_repo(tmp_path)
    stage1 = analyze_tree(tmp_path, SHA)
    llm = FakeLLM(responder=answer_with_misstatement)

    topics = await generate_topics(tmp_path, stage1, llm, target=3)

    assert topics and all(t.misstatement is not None for t in topics)
    for call in llm.calls_for(TOPIC_PURPOSE):
        found = _ABSENT.search(_user(call.messages))
        assert found is not None
        name = json.loads(found.group(1))["name"]
        # 함수 이름은 레포에서 온 값이라 서버 문장에는 나오지 않습니다.
        assert name not in _UNTRUSTED.sub("", _user(call.messages))
    # 틀린 말 규칙은 시스템 프롬프트에 있습니다.
    assert "absent_function" in llm.calls_for(TOPIC_PURPOSE)[0].messages[0]["content"]


async def test_invalid_misstatement_keeps_topic_without_extra_call(tmp_path: Path) -> None:
    write_repo(tmp_path)
    stage1 = analyze_tree(tmp_path, SHA)
    # 모든 주제가 absent_call인데 함수 이름 없는 틀린 말을 돌려줍니다.
    llm = FakeLLM(responder=lambda *_: FakeStep(material()))

    topics = await generate_topics(tmp_path, stage1, llm, target=3)

    assert len(topics) == 3
    assert all(t.misstatement is None for t in topics)
    # 틀린 말 때문에 다시 요청하지 않습니다.
    assert len(llm.calls_for(TOPIC_PURPOSE)) == 3
