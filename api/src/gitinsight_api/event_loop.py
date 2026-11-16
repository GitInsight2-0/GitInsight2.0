"""이벤트 루프 선택. psycopg 3 비동기 연결이 Windows에서도 동작하게 합니다.

psycopg 3의 비동기 모드는 Windows의 기본 루프(ProactorEventLoop)를 지원하지 않습니다.
그래서 Windows에서는 SelectorEventLoop를 쓰고, 그 밖의 OS는 uvicorn 기본값(uvloop가 있으면 uvloop)을 씁니다.

- 서버: uvicorn --loop gitinsight_api.event_loop:server_loop_factory
- 테스트: api/tests/conftest.py가 Windows에서 Selector 루프 정책을 씁니다.

Windows Selector 루프는 asyncio 서브프로세스를 지원하지 않습니다. 서버와 워커는 asyncio 서브프로세스를 쓰지 않습니다
(워커의 git 호출은 asyncio.to_thread 안의 subprocess.run입니다).
"""

import asyncio
import sys

from uvicorn.loops.auto import auto_loop_factory


def server_loop_factory() -> asyncio.AbstractEventLoop:
    """uvicorn --loop에 넘기는 루프 팩토리입니다. 호출할 때마다 새 루프를 만듭니다."""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return auto_loop_factory(use_subprocess=False)()
