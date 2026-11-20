"""Java 지원 (#182): 심볼 추출, 후보 고르기, 틀린 말 고르기, 강조 범위 후보."""

import textwrap
from pathlib import Path

from gitinsight_contracts.analysis import MisstatementKind
from gitinsight_contracts.common import SymbolKind
from gitinsight_worker.candidates import (
    has_enough_logic,
    is_inactive_path,
    is_script_path,
    is_test_path,
    java_logic_line_count,
    select_candidates,
    shared_dirs,
)
from gitinsight_worker.focus import focus_candidates
from gitinsight_worker.java_symbols import extract_java
from gitinsight_worker.languages import JAVA, language_of
from gitinsight_worker.misstatement import MisstatementPlanner
from gitinsight_worker.stage1 import analyze_tree

from worker_test_support import write_repo

SHA = "a" * 40

SERVICE = textwrap.dedent(
    """\
    package com.example.demo.user;

    import com.example.demo.repo.UserRepository;

    @Service
    @RequiredArgsConstructor
    public class UserService implements Greeter {

        private final UserRepository repo;

        public UserService(UserRepository repo) {
            this.repo = repo;
        }

        @Override
        @Transactional
        public User create(String name) {
            if (name == null) {
                throw new IllegalArgumentException("name");
            }
            return repo.save(new User(name));
        }

        public User create(String name, int age) {
            return create(name);
        }

        abstract void nothing();

        static class Cache {
            void clear() {
                Runnable r = new Runnable() {
                    public void run() { }
                };
            }
        }
    }

    interface Greeter {
        String greet();

        default String hi() {
            return "hi";
        }
    }

    enum Color {
        RED;

        void paint() {
        }
    }

    record Point(int x, int y) {
        int sum() {
            return x + y;
        }
    }
    """
)


def by_id(source: str, path: str = "src/main/java/com/example/UserService.java"):
    result = extract_java(source.encode(), path, SHA)
    return {s.symbol_id.split("::")[1]: (s.kind, s.start_line, s.end_line) for s in result.symbols}


# ---- 심볼 추출 ----


def test_types_and_methods_with_annotation_lines_skipped() -> None:
    found = by_id(SERVICE)

    # 어노테이션(@Service, @Override 등) 줄은 빼고 선언 줄부터입니다.
    assert found["UserService"] == (SymbolKind.CLASS, 7, 37)
    assert found["UserService.UserService"] == (SymbolKind.METHOD, 11, 13)
    assert found["UserService.create"] == (SymbolKind.METHOD, 17, 22)
    # 내부 클래스와 그 메서드, 인터페이스의 default 메서드, enum과 record의 메서드도 내보냅니다.
    assert found["UserService.Cache"] == (SymbolKind.CLASS, 30, 36)
    assert found["UserService.Cache.clear"] == (SymbolKind.METHOD, 31, 35)
    assert found["Greeter.hi"] == (SymbolKind.METHOD, 42, 44)
    assert found["Color.paint"] == (SymbolKind.METHOD, 50, 51)
    assert found["Point.sum"] == (SymbolKind.METHOD, 55, 57)


def test_bodiless_overloaded_and_anonymous_methods_are_not_exported() -> None:
    result = extract_java(SERVICE.encode(), "A.java", SHA)
    ids = {s.symbol_id.split("::")[1] for s in result.symbols}

    # 추상 메서드와 인터페이스의 선언만 있는 메서드는 내보내지도 세지도 않습니다.
    assert "UserService.nothing" not in ids and "Greeter.greet" not in ids
    # 오버로드는 처음 것만 내보냅니다. 익명 클래스의 메서드는 내보내지 않고 셉니다.
    assert sum(i == "UserService.create" for i in ids) == 1
    assert "UserService.Cache.clear.run" not in ids
    exported = len(result.symbols)
    # 셈에는 내보낸 것에 더해 오버로드된 create와 익명 클래스의 run이 들어갑니다. 본문 없는 메서드는 빠집니다.
    assert result.definition_count == exported + 2


def test_language_and_paths() -> None:
    assert language_of("src/main/java/A.java") == JAVA
    assert not is_script_path("A.java")
    assert is_test_path("src/test/java/com/x/UserServiceTest.java")
    assert is_test_path("src/main/java/com/x/UserServiceTests.java")
    assert is_test_path("src/main/java/com/x/UserServiceIT.java")
    assert not is_test_path("src/main/java/com/x/UserService.java")


# ---- 후보 고르기 ----


def test_field_only_and_accessor_classes_are_hollow() -> None:
    entity = textwrap.dedent(
        """\
        @Entity
        @Getter @Setter
        public class User {
            @Id @GeneratedValue
            private Long id;
            private String name;
            private String email;
            private int age = 0;
            private LocalDateTime createdAt;

            public String getName() {
                return name;
            }

            public void setName(String name) {
                this.name = name;
            }
        }
        """
    )
    service = SERVICE.split("abstract void")[0]

    assert java_logic_line_count(entity) == 0
    assert not has_enough_logic(entity, "User.java")
    assert has_enough_logic(service, "UserService.java")


def test_package_folder_shared_by_whole_repo_is_not_inactive() -> None:
    # spring-petclinic처럼 모든 코드가 `samples` 패키지 아래에 있어도 예제 폴더로 보지 않습니다.
    paths = [
        "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java",
        "src/main/java/org/springframework/samples/petclinic/vet/Vet.java",
        "src/test/java/org/springframework/samples/petclinic/owner/OwnerControllerTests.java",
    ]
    shared = shared_dirs(paths)

    assert shared == ("src", "main", "java", "org", "springframework", "samples", "petclinic")
    assert not is_inactive_path(paths[0], shared)
    # 패키지 이름 앞부분(com.example.demo)의 example은 예제 코드가 아닙니다. 프론트 폴더가 같이 있어 공통 폴더가 없어도 같습니다.
    mixed = ["src/main/java/com/example/demo/OrderService.java", "web/src/order.ts"]
    assert not is_inactive_path(mixed[0], shared_dirs(mixed))
    # 그보다 깊은 패키지나 레포의 다른 example 폴더는 여전히 지금 쓰지 않는 코드입니다.
    assert is_inactive_path("src/main/java/com/acme/shop/legacy/Old.java")
    assert is_inactive_path("examples/demo/src/main/java/com/acme/Demo.java", shared_dirs(mixed))


LOGIC = "".join(f"        int v{i} = compute({i});\n" for i in range(8))


def _java_class(name: str, method: str, extra: str = "") -> str:
    return (
        f"package com.example.demo.{name.lower()};\n\n{extra}"
        f"public class {name} {{\n    public int {method}() {{\n{LOGIC}"
        "        return v0;\n    }\n}\n"
    )


def test_candidates_skip_entities_and_tests(tmp_path: Path) -> None:
    base = "src/main/java/com/example/demo"
    write_repo(
        tmp_path,
        {
            f"{base}/service/OrderService.java": _java_class("OrderService", "placeOrder"),
            f"{base}/entity/Order.java": "public class Order {\n"
            + "".join(f"    private String f{i};\n" for i in range(12))
            + "}\n",
            "src/test/java/com/example/demo/OrderServiceTest.java": _java_class(
                "OrderServiceTest", "testPlaceOrder"
            ),
        },
    )
    symbols = analyze_tree(tmp_path, SHA).symbols

    chosen = [c.symbol.symbol_id for c in select_candidates(symbols)]

    assert any("OrderService.java::OrderService" in c for c in chosen)
    assert not any("OrderServiceTest" in c for c in chosen)


# ---- 틀린 말 고르기 ----


def test_misstatement_picks_java_method_of_imported_class(tmp_path: Path) -> None:
    base = "src/main/java/com/example/demo"
    controller = _java_class(
        "OrderController",
        "handle",
        extra="import com.example.demo.service.OrderService;\n\n",
    ).replace("compute(0)", "service.placeOrder()")
    service = _java_class("OrderService", "placeOrder").replace(
        "    }\n}\n",
        "    }\n\n    public int cancelOrder() {\n" + LOGIC + "        return v1;\n    }\n"
        "\n    public String getName() {\n        return name;\n    }\n}\n",
    )
    write_repo(
        tmp_path,
        {
            f"{base}/web/OrderController.java": controller,
            f"{base}/service/OrderService.java": service,
            "web/src/order.ts": "export function cancelOrderTs() {\n"
            + "  const a = 1;\n" * 8
            + "}\n",
        },
    )
    stage1 = analyze_tree(tmp_path, SHA)
    target = next(s for s in stage1.symbols if s.symbol_id.endswith("OrderController.handle"))

    plan = MisstatementPlanner(tmp_path, stage1.symbols, stage1.references).plan(target)

    # placeOrder는 실제로 부르고, getName은 getter라 빼고, TS 함수는 다른 언어라 빼서 cancelOrder가 남습니다.
    assert plan.kind is MisstatementKind.ABSENT_CALL
    assert plan.absent_symbol is not None
    assert plan.absent_symbol.symbol_id.endswith("OrderService.java::OrderService.cancelOrder")


# ---- 강조 범위 후보 ----


def test_focus_candidates_for_java_method_and_class() -> None:
    method_src = textwrap.dedent(
        """\
        public class Svc {
            public int run(List<Integer> xs) {
                int total = 0;
                int count = 0;
                int max = 0;
                if (xs.isEmpty()) {
                    log.info("empty");
                    return 0;
                }
                for (int x : xs) {
                    total += x;
                    count++;
                }
                xs.forEach(x -> {
                    max = Math.max(max, x);
                    record(x);
                });
                try (var c = open()) {
                    c.write(total);
                    c.flush();
                }
                return total;
            }
        }
        """
    )
    result = extract_java(method_src.encode(), "Svc.java", SHA)
    run = next(s for s in result.symbols if s.name == "run")
    svc = next(s for s in result.symbols if s.name == "Svc")

    blocks = [(f.start_line, f.end_line) for f in focus_candidates(method_src.encode(), run)]

    # 지역 변수 세 줄, if, for, 여러 줄 람다, try-with-resources가 각각 블록입니다.
    assert blocks == [(3, 5), (6, 9), (10, 13), (14, 17), (18, 22)]
    # 클래스 주제는 메서드 하나가 블록 하나입니다. 메서드가 클래스 거의 전부라 그 안으로 들어가 같은 블록이 나옵니다.
    assert [
        (f.start_line, f.end_line) for f in focus_candidates(method_src.encode(), svc)
    ] == blocks
