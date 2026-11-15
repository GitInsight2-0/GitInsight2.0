from pathlib import Path

import pytest

from api_test_support import ClientFactory, client_for_app


@pytest.fixture(autouse=True)
def no_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # 기본 테스트는 DB 없이 돕니다. 개발자 .env의 DATABASE_URL보다 환경 변수가 우선합니다.
    # 실제 Postgres 테스트는 TEST_DATABASE_URL과 -m postgres로 따로 돌립니다 (test_postgres.py).
    monkeypatch.setenv("DATABASE_URL", "")


@pytest.fixture
def client_for() -> ClientFactory:
    return client_for_app


@pytest.fixture
def metrics_path(tmp_path: Path) -> Path:
    return tmp_path / "metrics.jsonl"
