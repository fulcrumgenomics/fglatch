"""Tests for the authoritative workspace resolver."""

import logging

import pytest
from pytest import MonkeyPatch

from fglatch._tools.image import _workspace
from fglatch._tools.image._workspace import resolve_workspace


class _FakeUserConfig:
    """Stand-in for latch's persisted user config."""

    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id


def _set_ambient(monkeypatch: MonkeyPatch, *, env: str | None, config: str) -> None:
    if env is None:
        monkeypatch.delenv("LATCH_WORKSPACE", raising=False)
    else:
        monkeypatch.setenv("LATCH_WORKSPACE", env)
    monkeypatch.setattr(_workspace, "user_config", _FakeUserConfig(config))


# ambient source -> the value resolve_workspace must agree with.
AGREES: dict[str, tuple[str | None, str]] = {
    "env matches": ("123456", ""),
    "config matches": (None, "123456"),
    "env wins over config": ("123456", "999999"),
    "ambient unset": (None, ""),
}


@pytest.mark.parametrize("env, config", AGREES.values(), ids=AGREES.keys())
def test_resolve_returns_and_exports_when_ambient_agrees_or_unset(
    monkeypatch: MonkeyPatch, env: str | None, config: str
) -> None:
    """resolve_workspace() returns and exports the workspace when ambient agrees or unset."""
    _set_ambient(monkeypatch, env=env, config=config)
    assert resolve_workspace(workspace="123456") == "123456"
    import os

    assert os.environ["LATCH_WORKSPACE"] == "123456"  # exported for downstream calls


CONFLICTS: dict[str, tuple[str | None, str]] = {
    "env conflicts": ("999999", ""),
    "config conflicts": (None, "999999"),
}


@pytest.mark.parametrize("env, config", CONFLICTS.values(), ids=CONFLICTS.keys())
def test_resolve_fails_when_ambient_conflicts(
    monkeypatch: MonkeyPatch, env: str | None, config: str
) -> None:
    """resolve_workspace() raises ValueError when ambient conflicts with the request."""
    _set_ambient(monkeypatch, env=env, config=config)
    with pytest.raises(ValueError, match="123456"):
        resolve_workspace(workspace="123456")


def test_resolve_warns_and_resolves_locally_when_ambient_unset(
    monkeypatch: MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Ambient unset resolves locally with a warning, not a network default query."""
    _set_ambient(monkeypatch, env=None, config="")
    with caplog.at_level(logging.WARNING, logger="fglatch._tools.image._workspace"):
        assert resolve_workspace(workspace="123456") == "123456"
    assert any("no ambient workspace" in record.message.lower() for record in caplog.records)
