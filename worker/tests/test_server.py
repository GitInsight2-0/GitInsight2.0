"""분석까지 여는 서버 진입점. DB에는 연결하지 않습니다 (엔진은 첫 쿼리 때 연결합니다)."""

from pathlib import Path
from typing import Any

from pydantic import SecretStr

from gitinsight_api.settings import Settings
from gitinsight_worker.server import create_server


def settings_for(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {"metrics_path": tmp_path / "m.jsonl", **overrides}
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


async def test_server_opens_analysis_with_database(tmp_path: Path) -> None:
    app = create_server(
        settings_for(
            tmp_path,
            lab_enabled=False,
            database_url=SecretStr("postgresql://u:p@127.0.0.1:9/none"),
        )
    )
    try:
        assert app.state.analysis_service is not None
    finally:
        await app.state.db_engine.dispose()


async def test_server_without_database_or_in_lab_has_no_analysis(tmp_path: Path) -> None:
    no_db = create_server(settings_for(tmp_path, lab_enabled=False, database_url=None))
    assert no_db.state.analysis_service is None
    lab = create_server(
        settings_for(
            tmp_path, lab_enabled=True, database_url=SecretStr("postgresql://u:p@127.0.0.1:9/none")
        )
    )
    try:
        assert lab.state.analysis_service is None
    finally:
        await lab.state.db_engine.dispose()
