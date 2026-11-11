from gitinsight_contracts.analysis import LanguageStat
from gitinsight_worker.languages import count_languages, language_of


def test_languages_sorted_by_count_then_name() -> None:
    paths = ["a.py", "b/c.py", "README.md", "ci.yml", "ci2.yaml", "x.sh", "app.toml"]

    assert count_languages(paths) == [
        LanguageStat(language="Python", file_count=2),
        LanguageStat(language="YAML", file_count=2),
        LanguageStat(language="Markdown", file_count=1),
        LanguageStat(language="Shell", file_count=1),
        LanguageStat(language="TOML", file_count=1),
    ]


def test_unknown_extensions_and_case() -> None:
    assert language_of("data.bin") is None
    assert language_of("Makefile") is None
    assert language_of("SCRIPT.PY") == "Python"
    assert count_languages(["a.bin", "LICENSE"]) == []
