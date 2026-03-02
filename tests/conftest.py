from __future__ import annotations


def pytest_addoption(parser):
    """Register asyncio_mode ini key when pytest-asyncio isn't installed.

    This keeps pyproject's shared pytest config portable across minimal
    environments that run unit tests without dev extras.
    """
    parser.addini(
        "asyncio_mode",
        "Compatibility shim when pytest-asyncio plugin is unavailable.",
        default="auto",
    )
