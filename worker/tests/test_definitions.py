"""다른 파일의 레포 정의 요약 (#66). 데모 레포의 extract_active_db_resources와 같은 모양을 작게 만들어 확인합니다."""

from pathlib import Path

from gitinsight_worker.definitions import (
    MAX_DEFINITIONS,
    external_definitions,
    external_definitions_text,
    summarize,
    top_level_index,
)
from gitinsight_worker.stage1 import analyze_tree

from worker_test_support import write_repo

SHA = "a" * 40

SERVICE = "\n".join(
    [
        "class Store:",
        "    LIMIT = 3",
        "",
        "    def __init__(self):",
        "        self._collection = make()",
        "",
        "    def search(",
        "        self,",
        "        query: str,",
        "    ) -> list[dict]:",
        "        return self._collection.query(query)",
        "",
        "",
        "def get_store() -> Store:",
        "    return Store()",
        "",
    ]
)

USER = "\n".join(
    [
        "from app.service import get_store",
        "",
        "",
        "def find_client():",
        "    store = get_store()",
        "    for name in ['_client', 'client']:",
        "        if hasattr(store, name):",
        "            return getattr(store, name)",
        "    return None",
        "",
    ]
)


def repo(tmp_path: Path, extra: dict[str, str] | None = None):
    write_repo(tmp_path, {"app/service.py": SERVICE, "app/user.py": USER, **(extra or {})})
    stage1 = analyze_tree(tmp_path, SHA, blame_budget_s=0)
    by_id = {s.symbol_id: s for s in stage1.symbols}
    return stage1, by_id, top_level_index(stage1.symbols)


def test_used_function_and_its_return_class_are_found(tmp_path: Path) -> None:
    _, by_id, index = repo(tmp_path)

    found = external_definitions(tmp_path, by_id["app/user.py::find_client"], index)

    # 주제 코드는 get_store만 부르지만, 반환 타입의 클래스까지 따라가야 속성 이름을 비교할 수 있습니다.
    assert [s.symbol_id for s in found] == ["app/service.py::get_store", "app/service.py::Store"]


def test_return_class_is_found_inside_optional_and_string_annotations(tmp_path: Path) -> None:
    factories = "\n".join(
        [
            "from typing import Optional",
            "",
            "",
            "def maybe_store() -> Optional['Store']:",
            "    return None",
            "",
            "",
            "def many_stores() -> list[Store]:",
            "    return []",
            "",
        ]
    )
    caller = "def run():\n    return maybe_store(), many_stores()\n"
    _, by_id, index = repo(tmp_path, {"app/factory.py": factories, "app/caller.py": caller})

    found = external_definitions(tmp_path, by_id["app/caller.py::run"], index)

    # Optional, list 같은 이름은 건너뛰고 레포 클래스인 첫 이름을 따라갑니다.
    assert [s.symbol_id for s in found] == [
        "app/factory.py::maybe_store",
        "app/service.py::Store",
        "app/factory.py::many_stores",
    ]


def test_return_class_in_the_topic_file_is_not_attached(tmp_path: Path) -> None:
    # 주제 코드와 같은 파일의 클래스는 관련 심볼로 이미 보이므로 붙이지 않습니다.
    caller = "\n".join(
        [
            "from app.service import get_store",
            "",
            "",
            "class Store:",
            "    pass",
            "",
            "",
            "def run():",
            "    return get_store()",
            "",
        ]
    )
    write_repo(
        tmp_path,
        {"app/service.py": "def get_store() -> Store:\n    return 1\n", "app/main.py": caller},
    )
    stage1 = analyze_tree(tmp_path, SHA, blame_budget_s=0)
    by_id = {s.symbol_id: s for s in stage1.symbols}

    found = external_definitions(
        tmp_path, by_id["app/main.py::run"], top_level_index(stage1.symbols)
    )

    assert [s.symbol_id for s in found] == ["app/service.py::get_store"]


def test_class_summary_has_attributes_and_signatures_but_no_body(tmp_path: Path) -> None:
    _, by_id, _ = repo(tmp_path)

    text = summarize(SERVICE.encode(), by_id["app/service.py::Store"])

    assert text is not None
    assert text.splitlines() == [
        "# app/service.py",
        "class Store:",
        "    # 속성: LIMIT, _collection",
        "    def __init__(self): ...",
        "    def search(self, query: str) -> list[dict]: ...",
    ]
    # 본문은 넣지 않습니다.
    assert "make()" not in text and "query(query)" not in text


def test_same_file_ambiguous_and_legacy_definitions_are_skipped(tmp_path: Path) -> None:
    extra = {
        # 같은 이름이 다른 파일 두 곳에 있으면 어느 것인지 몰라 붙이지 않습니다.
        "app/a.py": "def helper():\n    return 1\n",
        "app/b.py": "def helper():\n    return 2\n",
        # 지금 쓰지 않는 경로의 정의도 붙이지 않습니다.
        "app/legacy_tools.py": "def old_tool():\n    return 3\n",
        "app/caller.py": (
            "def local():\n    return 0\n\n\n"
            "def run():\n    return helper() + old_tool() + local()\n"
        ),
    }
    _, by_id, index = repo(tmp_path, extra)

    assert external_definitions(tmp_path, by_id["app/caller.py::run"], index) == []
    assert external_definitions_text(tmp_path, by_id["app/caller.py::run"], index) == ""


def test_definitions_are_capped(tmp_path: Path) -> None:
    extra = {f"app/m{i}.py": f"def f{i}():\n    return {i}\n" for i in range(MAX_DEFINITIONS + 2)}
    calls = " + ".join(f"f{i}()" for i in range(MAX_DEFINITIONS + 2))
    extra["app/caller.py"] = f"def run():\n    return {calls}\n"
    _, by_id, index = repo(tmp_path, extra)

    found = external_definitions(tmp_path, by_id["app/caller.py::run"], index)

    assert [s.name for s in found] == [f"f{i}" for i in range(MAX_DEFINITIONS)]


def test_script_files_are_not_linked(tmp_path: Path) -> None:
    # 정의 요약은 Python 문법으로 읽으므로 TypeScript·JavaScript와는 연결하지 않습니다 (#72).
    extra = {
        "web/store.ts": "export function get_store() {\n  return find_client();\n}\n",
    }
    _, by_id, index = repo(tmp_path, extra)

    # 같은 이름의 TS 함수가 있어도 Python 주제는 Python 정의를 그대로 찾습니다.
    assert [s.path for s in index["get_store"]] == ["app/service.py"]
    assert external_definitions_text(tmp_path, by_id["app/user.py::find_client"], index)
    # TS 주제에는 Python 정의를 붙이지 않습니다.
    assert external_definitions_text(tmp_path, by_id["web/store.ts::get_store"], index) == ""
