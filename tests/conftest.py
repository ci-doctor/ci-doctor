"""Air-gap guard: no test may open a real network connection. Plus one shared registry.

Everything is exercised against in-memory doubles, so any real socket connect is
a bug (a dependency that quietly started phoning home — exactly the regression
that would surface only in the one environment where it can't be debugged).
"""

import socket
from pathlib import Path
from typing import Never

import pytest

from tests import support


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block every real socket connection for the duration of a test.

    Autouse, so no test can opt out by forgetting to request it.

    Args:
        monkeypatch: pytest fixture used to patch the socket module.
    """

    def guard(*args: object, **kwargs: object) -> Never:
        """Stand in for socket connect/create_connection.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("network access is not allowed in tests (air-gap guarantee)")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


@pytest.fixture(autouse=True)
def _user_config_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the user config at an empty directory, so the developer's own never leaks in.

    Args:
        tmp_path_factory: pytest fixture for a fresh directory.
        monkeypatch: pytest fixture used to patch click's app dir.

    Returns:
        The directory `config.yml` lives in; tests that need a user config write it there.
    """
    home = tmp_path_factory.mktemp("user-config")
    monkeypatch.setattr("click.get_app_dir", lambda _name: str(home))
    return home


@pytest.fixture(scope="session")
def fx_registry(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The two-matcher Python registry from `tests.support`, shared read-only.

    Args:
        tmp_path_factory: pytest fixture for a session-wide directory.

    Returns:
        The registry directory. Tests that edit a registry write their own.
    """
    return support.sign_registry(support.py_registry(tmp_path_factory.mktemp("registry")))


@pytest.fixture(autouse=True)
def _private_cache(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets its own registry cache, never the user's.

    Args:
        tmp_path_factory: pytest fixture for a fresh directory.
        monkeypatch: pytest fixture to swap the default cache location.
    """
    root = tmp_path_factory.mktemp("cache")
    monkeypatch.setattr("ci_doctor.matchers.cache.default_root", lambda: root)


@pytest.fixture(autouse=True)
def _test_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The official registry's index is checked against the test key, and only it.

    Args:
        monkeypatch: pytest fixture to swap the trusted keys.
    """
    monkeypatch.setattr(
        "ci_doctor.matchers.signing.OFFICIAL_KEYS", {support.TEST_KEY_ID: support.TEST_PUBLIC_KEY}
    )
