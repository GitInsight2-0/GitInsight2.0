"""주제 심볼 안의 강조 범위 후보 (#156)."""

import json
import textwrap

from gitinsight_api.llm.fake import FakeLLM, FakeStep
from gitinsight_contracts.analysis import ContributionLabel
from gitinsight_worker import stage2
from gitinsight_worker.candidates import select_candidates
from gitinsight_worker.focus import MAX_CANDIDATES, MIN_SYMBOL_LINES, focus_candidates
from gitinsight_worker.stage1 import analyze_tree
from gitinsight_worker.stage2 import (
    TOPIC_PURPOSE,
    TopicContext,
    build_topic_messages,
    generate_topics,
    point_focus,
    read_symbol_code,
    validate_material,
    visible_focus,
)

from worker_test_support import material, write_repo

SHA = "a" * 40
CTX = TopicContext("handle", ContributionLabel.SHARED, [])


def candidates(tmp_path, path: str, source: str, symbol_id: str):
    text = textwrap.dedent(source)
    write_repo(tmp_path, {path: text})
    symbol = next(s for s in analyze_tree(tmp_path, SHA).symbols if s.symbol_id == symbol_id)
    found = focus_candidates(text.encode(), symbol)
    for c in found:
        # 줄 번호는 늘 주제 심볼 범위 안에서만 나옵니다.
        assert symbol.start_line <= c.start_line <= c.end_line <= symbol.end_line
    return symbol, [(c.focus_id, c.start_line, c.end_line, c.head) for c in found]


PY_FUNCTION = """\
    def handle(request):
        \"\"\"요청을 처리합니다.\"\"\"
        user = request.user
        data = request.data
        # 캐시를 먼저 봅니다.
        if user in CACHE:
            hit = CACHE[user]
            log(hit)
            return hit
        for item in data:
            check(item)
            save(item)
            count(item)
        try:
            result = compute(data)
            store(result)
        except ValueError:
            result = None
            report()
        return result
    """


def test_python_function_is_split_into_statement_blocks(tmp_path) -> None:
    _, found = candidates(tmp_path, "app/handler.py", PY_FUNCTION, "app/handler.py::handle")

    # 문서 문자열과 주석은 빼고, 짧은 대입 두 줄은 다음 블록(if)과 합칩니다.
    assert found == [
        ("b1", 3, 9, "user = request.user"),
        ("b2", 10, 13, "for item in data:"),
        ("b3", 14, 20, "try:"),
    ]


def test_python_class_is_split_into_methods(tmp_path) -> None:
    source = """\
        class Service:
            def __init__(self):
                self.a = 1
                self.b = 2
                self.c = 3

            @property
            def size(self):
                total = self.a + self.b
                total += self.c
                return total

            def run(self):
                first = self.a
                second = self.b
                third = self.c
                return first + second + third
        """
    _, found = candidates(tmp_path, "app/service.py", source, "app/service.py::Service")

    # 데코레이터 줄은 넣지 않고 def 줄부터입니다 (주제 심볼과 같은 규칙).
    assert [(f, s, e) for f, s, e, _ in found] == [("b1", 2, 5), ("b2", 8, 11), ("b3", 13, 17)]


def test_block_covering_almost_everything_is_split_inside(tmp_path) -> None:
    body = "".join(f"            v{i} = {i}\n" for i in range(6))
    source = (
        "def stream(request):\n"
        "    async def events():\n"
        "        try:\n"
        + body
        + "            if v0:\n                yield v0\n                yield v1\n"
        + "        except KeyError:\n            yield None\n            yield v2\n"
        + "    return events()\n"
    )
    _, found = candidates(tmp_path, "app/api.py", source, "app/api.py::stream")

    # events()와 그 안의 try가 거의 전부라서, try 안의 문장과 except 본문까지 들어가 나눕니다.
    # 짧은 except 본문(14~15줄)과 return(16줄)은 앞의 if 블록과 합쳐집니다.
    assert [(s, e) for _, s, e, _ in found] == [(4, 9), (10, 16)]


def test_short_symbol_has_no_candidates(tmp_path) -> None:
    lines = "".join(f"    v{i} = {i}\n" for i in range(MIN_SYMBOL_LINES - 5))
    source = f"def short():\n    if True:\n        pass\n{lines}    return v0\n"
    symbol, found = candidates(tmp_path, "app/short.py", source, "app/short.py::short")

    assert symbol.end_line - symbol.start_line + 1 < MIN_SYMBOL_LINES
    assert found == []


def test_too_many_blocks_are_merged_to_the_limit(tmp_path) -> None:
    ifs = "".join(f"    if x == {i}:\n        a = {i}\n        b = {i}\n" for i in range(20))
    source = f"def many(x):\n{ifs}    return x\n"
    _, found = candidates(tmp_path, "app/many.py", source, "app/many.py::many")

    assert len(found) == MAX_CANDIDATES
    # 합쳐도 블록끼리 겹치지 않고 순서대로입니다.
    ends = [e for _, _, e, _ in found]
    starts = [s for _, s, _, _ in found]
    assert all(e < s for e, s in zip(ends, starts[1:], strict=False))


TSX_COMPONENT = """\
    export function Screen({ id }: { id: string }) {
      const [a, setA] = useState(0);
      const [b, setB] = useState(1);
      useEffect(() => {
        load(id);
        track(id);
      }, [id]);
      const onClick = () => {
        setA(a + 1);
        setB(b + 1);
      };
      if (!a) {
        return null;
        // 아직 불러오지 않았습니다.
      }
      return (
        <div onClick={onClick}>
          {a} {b}
        </div>
      );
    }
    """


def test_script_function_splits_hooks_inner_functions_and_returns(tmp_path) -> None:
    _, found = candidates(tmp_path, "web/Screen.tsx", TSX_COMPONENT, "web/Screen.tsx::Screen")

    assert [(s, e) for _, s, e, _ in found] == [(2, 7), (8, 11), (12, 15), (16, 20)]
    assert found[1][3] == "const onClick = () => {"


def test_script_class_is_split_into_methods(tmp_path) -> None:
    source = """\
        export class Meter {
          level = 0;

          start() {
            this.level = 1;
            this.tick();
            return this.level;
          }

          tick = () => {
            this.level += 1;
            this.emit();
          };

          stop() {
            this.level = 0;
            this.emit();
            return this.level;
          }
        }
        """
    _, found = candidates(tmp_path, "web/meter.ts", source, "web/meter.ts::Meter")

    # 값만 담은 필드(level)는 빼고 메서드와 함수를 담은 필드만 블록입니다.
    assert [(s, e) for _, s, e, _ in found] == [(4, 8), (10, 13), (15, 19)]


# ---- 2단계 요청과 검증 (#156) ----


def _long_repo(tmp_path):
    write_repo(tmp_path, {"app/handler.py": textwrap.dedent(PY_FUNCTION)})
    stage1 = analyze_tree(tmp_path, SHA)
    [candidate] = select_candidates(stage1.symbols)
    return stage1, candidate


def test_prompt_lists_candidates_without_line_numbers(tmp_path) -> None:
    _, candidate = _long_repo(tmp_path)
    code = read_symbol_code(tmp_path, candidate)
    focus = visible_focus(tmp_path, candidate, code)

    messages = build_topic_messages(candidate, code, "", focus=focus)
    user = "\n".join(m["content"] for m in messages if m["role"] == "user")

    block = user.split('<untrusted_data kind="focus_candidates">')[1].split("</untrusted_data>")[0]
    assert json.loads(block) == [
        {"id": "b1", "first_line": "user = request.user"},
        {"id": "b2", "first_line": "for item in data:"},
        {"id": "b3", "first_line": "try:"},
    ]
    # 줄 번호는 LLM에 보여 주지 않습니다.
    assert "start_line" not in block
    assert "focus_id" in messages[0]["content"]


def test_candidates_after_truncation_are_not_offered(tmp_path, monkeypatch) -> None:
    _, candidate = _long_repo(tmp_path)
    # 앞 9줄(3~9줄 if 블록까지)만 보내도록 잘라 봅니다.
    lines = textwrap.dedent(PY_FUNCTION).splitlines()
    cut = len("\n".join(lines[:9]))
    monkeypatch.setattr(stage2, "MAX_CODE_CHARS", cut)

    code = read_symbol_code(tmp_path, candidate)
    focus = visible_focus(tmp_path, candidate, code)

    assert [f.focus_id for f in focus] == ["b1"]


def test_chosen_ids_map_to_points_and_unknown_ids_are_dropped(tmp_path) -> None:
    _, candidate = _long_repo(tmp_path)
    code = read_symbol_code(tmp_path, candidate)
    focus = visible_focus(tmp_path, candidate, code)
    mat = material(focus_ids=["b2", "b9", None])
    topic = validate_material(mat, candidate, CTX)

    chosen = point_focus(mat, topic, focus)

    # 모르는 id(b9)와 null은 범위 없이 둡니다. 다시 요청하지 않습니다.
    assert {k: (v.start_line, v.end_line) for k, v in chosen.items()} == {"p1": (10, 13)}


async def test_focus_does_not_add_calls(tmp_path) -> None:
    stage1, _ = _long_repo(tmp_path)
    llm = FakeLLM(responder=lambda *_: FakeStep(material(focus_ids=["b1", "b2", "b3"])))

    topics = await generate_topics(tmp_path, stage1, llm, target=1)

    assert len(topics) == 1
    [call] = llm.calls_for(TOPIC_PURPOSE)
    assert "focus_candidates" in "\n".join(m["content"] for m in call.messages)
