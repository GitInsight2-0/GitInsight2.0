"""API 테스트 공용 도구. pytest 설정의 pythonpath로 테스트 모듈에서 import합니다."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
from fastapi import FastAPI

ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx.AsyncClient]]


@asynccontextmanager
async def client_for_app(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
