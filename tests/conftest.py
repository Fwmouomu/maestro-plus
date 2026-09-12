"""Shared fixtures.

`anyio` ships its own pytest plugin, so the only thing needed is to pin the
backend. Doing it explicitly keeps the tests runnable on a machine where trio is
not installed.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
