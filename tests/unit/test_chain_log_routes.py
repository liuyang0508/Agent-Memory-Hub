"""Threading contract for request-chain Web routes."""

from __future__ import annotations

import asyncio
import inspect
import threading
import time

import httpx
from fastapi import FastAPI


def test_chain_log_routes_are_sync_for_fastapi_threadpool_dispatch() -> None:
    from web.api.routes.chain_logs import chain_log_detail, chain_logs

    assert inspect.iscoroutinefunction(chain_logs) is False
    assert inspect.iscoroutinefunction(chain_log_detail) is False


def test_chain_log_list_and_detail_builders_can_run_concurrently(
    monkeypatch,
) -> None:
    from web.api.routes import chain_logs as routes
    from web.auth import CurrentUser, get_current_user

    active = 0
    maximum_active = 0
    lock = threading.Lock()

    class Payload:
        def to_dict(self) -> dict[str, bool]:
            return {"ok": True}

    def blocking_builder(*args, **kwargs):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.15)
        with lock:
            active -= 1
        return Payload()

    monkeypatch.setattr(routes, "build_chain_log_report", blocking_builder)
    monkeypatch.setattr(routes, "build_chain_log_detail", blocking_builder)
    app = FastAPI()
    app.include_router(routes.router)

    async def admin_user() -> CurrentUser:
        return CurrentUser("admin", "default", "admin")

    app.dependency_overrides[get_current_user] = admin_user

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = await asyncio.gather(
                client.get("/api/chain-logs"),
                client.get("/api/chain-logs/chain-test"),
            )
        assert [response.status_code for response in responses] == [200, 200]

    asyncio.run(exercise())

    assert maximum_active == 2
