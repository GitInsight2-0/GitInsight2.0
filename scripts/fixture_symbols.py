"""샘플 데이터 작성을 돕는 도구입니다. 로컬 clone에서 함수 줄 범위와 blame 작성자 비율을 출력합니다.

사용: uv run python scripts/fixture_symbols.py <clone 경로> <파일 경로> [<파일 경로> ...]
실제 분석 엔진(A의 T6·T11)을 대신하지 않습니다. 샘플 데이터의 줄 범위를 손으로 적지 않기 위한 도구입니다.
"""

import ast
import subprocess
import sys
from collections import Counter
from pathlib import Path


def blame_authors(repo: Path, path: str, start: int, end: int) -> Counter[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "blame", "--line-porcelain", "-w", "-L", f"{start},{end}", path],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    return Counter(
        line[len("author ") :] for line in out.splitlines() if line.startswith("author ")
    )


def main(argv: list[str]) -> int:
    repo = Path(argv[0])
    total_symbols = 0
    for py in repo.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        total_symbols += sum(
            isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            for n in ast.walk(tree)
        )
    print(f"symbol_count={total_symbols}")
    for rel in argv[1:]:
        tree = ast.parse((repo / rel).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                end = node.end_lineno or node.lineno
                authors = blame_authors(repo, rel, node.lineno, end)
                share = ", ".join(f"{a}:{c}" for a, c in authors.most_common())
                print(f"{rel}::{node.name} {node.lineno}-{end} [{share}]")
                if isinstance(node, ast.ClassDef):
                    for child in node.body:
                        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                            child_end = child.end_lineno or child.lineno
                            print(f"  {rel}::{node.name}.{child.name} {child.lineno}-{child_end}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
