"""TypeScript·JavaScript 심볼 추출 (#72)."""

import textwrap

from gitinsight_contracts.common import SymbolKind
from gitinsight_worker.script_symbols import extract_script, identifier_lines, parse_script
from gitinsight_worker.symbols import MAX_SYMBOL_ID_LENGTH

SHA = "a" * 40

TS_SOURCE = textwrap.dedent(
    """\
    import { helper } from "./helper";

    export function top(a: number, b: number): number {
      function inner() {
        return 1;
      }
      return inner() + a + b;
    }

    export const arrow = async (id: string) => {
      const nested = () => id;
      return nested();
    };

    @Injectable()
    export class Service extends Base {
      private limit = 3;

      constructor(private readonly repo: Repo) {
        super();
      }

      @Get("/items")
      // 목록을 돌려줍니다.
      async list(): Promise<Item[]> {
        return this.repo.all();
      }

      handle = (event: Event) => {
        return event.type;
      };

      static #secret() {
        return 1;
      }

      [Symbol.iterator]() {
        return this.items();
      }
    }

    export default function main() {
      return top(1, 2);
    }

    const legacy = function named() {
      return 0;
    };

    export abstract class Shape {
      abstract area(): number;

      describe(): string {
        return "shape";
      }
    }

    function over(a: string): void;
    function over(a: unknown): void {
      helper(a);
    }

    declare function ambient(): void;

    interface Options {
      run(): void;
    }

    const config = {
      build() {
        return 1;
      },
      watch: () => 2,
    };

    export const Wrapped = memo(() => {
      return null;
    });

    const Anonymous = class {
      run() {
        return 1;
      }
    };
    """
).encode()


def by_id(source: bytes = TS_SOURCE, path: str = "src/mod.ts"):
    result = extract_script(source, path, SHA)
    return {s.symbol_id: (s.kind, s.start_line, s.end_line) for s in result.symbols}


def test_functions_classes_and_methods_have_exact_line_range() -> None:
    symbols = by_id()

    assert symbols == {
        "src/mod.ts::top": (SymbolKind.FUNCTION, 3, 8),
        "src/mod.ts::top.inner": (SymbolKind.FUNCTION, 4, 6),
        "src/mod.ts::arrow": (SymbolKind.FUNCTION, 10, 13),
        "src/mod.ts::arrow.nested": (SymbolKind.FUNCTION, 11, 11),
        "src/mod.ts::Service": (SymbolKind.CLASS, 16, 40),
        "src/mod.ts::Service.constructor": (SymbolKind.METHOD, 19, 21),
        "src/mod.ts::Service.list": (SymbolKind.METHOD, 25, 27),
        "src/mod.ts::Service.handle": (SymbolKind.METHOD, 29, 31),
        "src/mod.ts::Service.#secret": (SymbolKind.METHOD, 33, 35),
        "src/mod.ts::main": (SymbolKind.FUNCTION, 42, 44),
        "src/mod.ts::legacy": (SymbolKind.FUNCTION, 46, 48),
        "src/mod.ts::Shape": (SymbolKind.CLASS, 50, 56),
        "src/mod.ts::Shape.describe": (SymbolKind.METHOD, 53, 55),
        "src/mod.ts::over": (SymbolKind.FUNCTION, 59, 61),
        "src/mod.ts::Anonymous": (SymbolKind.CLASS, 80, 84),
        "src/mod.ts::Anonymous.run": (SymbolKind.METHOD, 81, 83),
    }


def test_ranges_cover_the_definition_text() -> None:
    # 줄 범위의 첫 줄에는 이름이, 마지막 줄에는 닫는 괄호가 있어야 합니다. 한 줄짜리 정의는 첫 줄만 봅니다.
    lines = TS_SOURCE.decode().splitlines()
    result = extract_script(TS_SOURCE, "src/mod.ts", SHA)

    for symbol in result.symbols:
        assert symbol.name in lines[symbol.start_line - 1], symbol.symbol_id
        if symbol.end_line > symbol.start_line:
            assert lines[symbol.end_line - 1].strip() in ("}", "};"), symbol.symbol_id


def test_decorator_lines_are_not_part_of_the_range() -> None:
    symbols = by_id()

    # 데코레이터는 15줄과 23줄에, 그 뒤 주석은 24줄에 있습니다.
    assert symbols["src/mod.ts::Service"][1] == 16
    assert symbols["src/mod.ts::Service.list"][1] == 25


def test_functions_directly_inside_a_top_level_function_are_emitted() -> None:
    result = extract_script(TS_SOURCE, "src/mod.ts", SHA)
    ids = {s.symbol_id for s in result.symbols}

    assert {"src/mod.ts::top.inner", "src/mod.ts::arrow.nested"} <= ids
    # 정의 수는 내보낸 것과 상관없이 이름 있는 정의 전체입니다.
    assert result.definition_count == 16
    assert len(result.symbols) == 16


COMPONENT = textwrap.dedent(
    """    export function Profile({ id }: Props) {
      const [name, setName] = useState("");

      useEffect(() => {
        const load = async () => {
          const parse = (raw: string) => raw.trim();
          setName(parse(await fetchName(id)));
        };
        load();
      }, [id]);

      const handleSave = async () => {
        await save(id, name);
      };

      function reset() {
        setName("");
      }

      class Local {
        run() {
          return 1;
        }
      }

      return <button onClick={handleSave}>{name}</button>;
    }

    export class Store {
      save() {
        const inner = () => 1;
        return inner();
      }
    }

    describe("profile", () => {
      function helper() {
        return 1;
      }
    });
    """
).encode()


def test_component_handlers_are_emitted_but_deeper_definitions_are_not() -> None:
    result = extract_script(COMPONENT, "web/Profile.tsx", SHA)

    assert {s.symbol_id: (s.kind, s.start_line, s.end_line) for s in result.symbols} == {
        "web/Profile.tsx::Profile": (SymbolKind.FUNCTION, 1, 27),
        # useEffect 콜백처럼 이름 없는 함수 안에 있어도 컴포넌트 바로 아래 이름으로 내보냅니다.
        "web/Profile.tsx::Profile.load": (SymbolKind.FUNCTION, 5, 8),
        "web/Profile.tsx::Profile.handleSave": (SymbolKind.FUNCTION, 12, 14),
        "web/Profile.tsx::Profile.reset": (SymbolKind.FUNCTION, 16, 18),
        "web/Profile.tsx::Store": (SymbolKind.CLASS, 29, 34),
        "web/Profile.tsx::Store.save": (SymbolKind.METHOD, 30, 33),
    }
    # 내보내지 않은 것: load 안의 parse, 함수 안의 클래스 Local과 run, 메서드 안의 inner, 이름 없는 콜백 안의 helper
    assert result.definition_count == 11


def test_nested_functions_follow_their_parent_in_source_order() -> None:
    result = extract_script(COMPONENT, "web/Profile.tsx", SHA)

    assert [s.start_line for s in result.symbols] == sorted(s.start_line for s in result.symbols)


def test_declarations_without_body_and_unnamed_code_are_skipped() -> None:
    names = {symbol_id.split("::")[1] for symbol_id in by_id()}

    # 추상 메서드, declare, 인터페이스, 객체 리터럴의 메서드, 호출로 감싼 함수는 내보내지 않습니다.
    assert names.isdisjoint(
        {"Shape.area", "ambient", "Options", "Options.run", "config", "build", "watch", "Wrapped"}
    )


def test_references_are_not_counted() -> None:
    result = extract_script(TS_SOURCE, "src/mod.ts", SHA)

    assert not result.references
    assert result.self_references == {}
    assert result.implicitly_called == frozenset()


JS_SOURCE = textwrap.dedent(
    """\
    const express = require("express");

    class Router {
      @bound
      route(path) {
        return path;
      }

      get size() {
        return 1;
      }

      set size(value) {
        this._size = value;
      }

      onError = (error) => {
        throw error;
      };
    }

    function* walk(tree) {
      yield tree;
    }

    exports.create = function () {
      return new Router();
    };

    module.exports.destroy = (router) => {
      router.close();
    };

    module.exports = function () {
      return 1;
    };

    app.get("/", (req, res) => {
      function respond() {
        res.send("ok");
      }
      respond();
    });

    export default class {
      hidden() {
        return 1;
      }
    }
    """
).encode()


def test_javascript_classes_generators_and_commonjs_exports() -> None:
    assert by_id(JS_SOURCE, "lib/router.js") == {
        "lib/router.js::Router": (SymbolKind.CLASS, 3, 20),
        # 데코레이터가 메서드 노드 안에 있어도 범위는 이름 줄에서 시작합니다.
        "lib/router.js::Router.route": (SymbolKind.METHOD, 5, 7),
        # getter와 setter는 이름이 같아 처음 것만 내보냅니다.
        "lib/router.js::Router.size": (SymbolKind.METHOD, 9, 11),
        "lib/router.js::Router.onError": (SymbolKind.METHOD, 17, 19),
        "lib/router.js::walk": (SymbolKind.FUNCTION, 22, 24),
        "lib/router.js::create": (SymbolKind.FUNCTION, 26, 28),
        "lib/router.js::destroy": (SymbolKind.FUNCTION, 30, 32),
    }


def test_tsx_and_jsx_components_are_extracted_as_functions() -> None:
    source = textwrap.dedent(
        """\
        export function Button({ label }: Props) {
          return <button type="button">{label}</button>;
        }

        export const Card = ({ children }: Props) => (
          <div className="card">
            {children}
          </div>
        );

        export function identity<T>(value: T): T {
          return value;
        }
        """
    ).encode()

    assert by_id(source, "web/Button.tsx") == {
        "web/Button.tsx::Button": (SymbolKind.FUNCTION, 1, 3),
        "web/Button.tsx::Card": (SymbolKind.FUNCTION, 5, 9),
        "web/Button.tsx::identity": (SymbolKind.FUNCTION, 11, 13),
    }
    jsx = b"export const App = () => <main>hi</main>;\n"
    assert by_id(jsx, "web/App.jsx") == {"web/App.jsx::App": (SymbolKind.FUNCTION, 1, 1)}


def test_type_assertion_syntax_is_read_as_typescript_in_ts_files() -> None:
    # `<T>value`는 .ts에서는 타입 단언이고 .tsx에서는 JSX입니다. 확장자로 문법을 고릅니다.
    source = b"export function cast(value: unknown) {\n  return <string>value;\n}\n"

    assert by_id(source, "src/cast.ts") == {"src/cast.ts::cast": (SymbolKind.FUNCTION, 1, 3)}


def test_syntax_error_still_returns_valid_symbols() -> None:
    source = b"function ok() {\n  return 1;\n}\n\nfunction broken( {\n"

    assert by_id(source, "a.js")["a.js::ok"] == (SymbolKind.FUNCTION, 1, 3)


def test_empty_non_utf8_and_crlf_sources() -> None:
    assert extract_script(b"", "a.ts", SHA).symbols == []
    assert extract_script(b"const x = 1;\n", "a.ts", SHA).definition_count == 0
    cp949 = "// 주석\nfunction f() {\n  return '한';\n}\n".encode("cp949")
    assert by_id(cp949, "a.js") == {"a.js::f": (SymbolKind.FUNCTION, 2, 4)}
    crlf = b"function f() {\r\n  return 1;\r\n}\r\n"
    assert by_id(crlf, "a.js") == {"a.js::f": (SymbolKind.FUNCTION, 1, 3)}


def test_symbol_with_too_long_id_is_skipped() -> None:
    path = "d/" * 120 + "m.ts"

    result = extract_script(b"function f() {}\n", path, SHA)

    assert len(path) > MAX_SYMBOL_ID_LENGTH
    assert result.symbols == []
    assert result.definition_count == 1


def test_very_deep_expression_does_not_hit_recursion_limit() -> None:
    source = ("const x = " + " + ".join(["1"] * 5000) + ";\n\nfunction f() {}\n").encode()

    assert "a.ts::f" in by_id(source, "a.ts")


def names(source: str, path: str) -> list[tuple[str, int]]:
    return list(identifier_lines(parse_script(textwrap.dedent(source).encode(), path).root_node))


def test_identifiers_skip_definition_names_and_imports() -> None:
    source = """\
import { helper } from "./helper";
        export { reexported } from "./other";

        export function top(repo: Repo): Item[] {
          const inner = () => helper(repo);
          return repo.all().map(inner);
        }

        class Service {
          handle = (event: Event) => this.save(event);
          save(event: Event) {
            return store.put(event);
          }
        }

        exports.legacy = function () {
          return top(null);
        };
        """

    assert names(source, "a.ts") == [
        ("repo", 4),
        ("Repo", 4),
        ("Item", 4),
        ("helper", 5),
        ("repo", 5),
        ("repo", 6),
        ("all", 6),
        ("map", 6),
        ("inner", 6),
        ("event", 10),
        ("Event", 10),
        ("save", 10),
        ("event", 10),
        ("event", 11),
        ("Event", 11),
        ("store", 12),
        ("put", 12),
        ("event", 12),
        ("exports", 16),
        ("top", 17),
    ]


def test_identifiers_include_jsx_destructuring_and_private_names() -> None:
    source = """\
function View({ items }) {
          const { first, rest: others } = split(items);
          return <List.Item value={first} {...others} />;
        }

        class Counter {
          #count = 0;
          bump() {
            return this.#count++;
          }
        }
        """

    found = names(source, "a.tsx")

    assert ("items", 1) in found
    assert ("first", 2) in found
    assert ("rest", 2) in found
    assert ("split", 2) in found
    assert ("List", 3) in found
    assert ("Item", 3) in found
    assert ("value", 3) in found
    assert ("#count", 7) in found
    assert ("#count", 9) in found
    assert "View" not in {name for name, _ in found}
    assert "bump" not in {name for name, _ in found}


def test_identifiers_skip_labels_and_read_by_suffix() -> None:
    js = """\
outer: for (const x of xs) {
          if (check(x)) break outer;
        }
        """

    assert names(js, "a.js") == [("x", 1), ("xs", 1), ("check", 2), ("x", 2)]
    # .ts 문법은 `<T>x` 형변환을 읽고, 같은 소스를 .tsx로 읽으면 JSX로 봅니다.
    assert ("T", 1) in names("const y = <T>value;\n", "a.ts")


def test_identifiers_handle_deep_expression() -> None:
    source = "const x = " + " + ".join(["n"] * 5000) + ";\n"

    # 함수가 아닌 값을 담은 변수 이름(x)은 Python처럼 식별자로 셉니다.
    assert len(names(source, "a.ts")) == 5001
