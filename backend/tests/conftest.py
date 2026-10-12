"""Shared fixtures. The MCP servers started here are the DEMO mocks (synthetic data); tests that
use them are contract tests of our connector code, not proof that the real upstreams work."""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
from collections.abc import Iterator

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SCHEDULER_ENABLED", "false")


def _free_base_port() -> int:
    for _ in range(50):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port > 60000:
            continue
        try:
            for off in (1, 2):
                with socket.socket() as t:
                    t.bind(("127.0.0.1", port + off))
            return port
        except OSError:
            continue
    raise RuntimeError("no free port triple")


@pytest.fixture(scope="session")
def mock_mcp() -> Iterator[dict[str, str]]:
    from app.devtools import mock_mcp as mm

    port = _free_base_port()
    servers = mm.make_uvicorn_servers(port)
    loop = asyncio.new_event_loop()

    def run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(asyncio.gather(*(s.serve() for s in servers)))

    th = threading.Thread(target=run, daemon=True)
    th.start()
    deadline = time.time() + 20
    while not all(s.started for s in servers):
        if time.time() > deadline:
            raise RuntimeError("mock MCP servers did not start")
        time.sleep(0.05)
    yield {"bhavcopy": f"http://127.0.0.1:{port}/mcp", "cm": f"http://127.0.0.1:{port + 1}/mcp",
           "kite": f"http://127.0.0.1:{port + 2}/mcp", "kite_authorize": f"http://127.0.0.1:{port + 2}/authorize?token=demo",
           "port": str(port)}
    for s in servers:
        s.should_exit = True
    th.join(timeout=5)


@pytest.fixture
def mock_state(mock_mcp):
    from app.devtools.mock_mcp import STATE

    STATE.kite_logged_in = False
    STATE.forbidden_calls.clear()
    STATE.calls.clear()
    STATE.fail_next.clear()
    return STATE
