from importlib.metadata import version
from inspect import isfunction
from typing import Callable

import pytest
from defopt import signature
from pytest import CaptureFixture
from pytest import MonkeyPatch

import fglatch
from fglatch import _main


@pytest.mark.parametrize("tool", _main.TOOLS)
def test_tools_are_defined(tool: Callable) -> None:
    """Test that all command line tools passed to defopt are defined functions."""
    assert isfunction(tool)


@pytest.mark.parametrize("tool", _main.TOOLS)
def test_tools_have_valid_docstrings(tool: Callable) -> None:
    """Test that all command line tools have a valid defopt docstring."""
    try:
        signature(tool)
    except TypeError:
        raise AssertionError(f"defopt could not parse docstring for {tool.__name__}") from None


def test_version_attribute_matches_package_metadata() -> None:
    """The exported `__version__` should track the installed package version."""
    assert fglatch.__version__ == version("fglatch")


def test_cli_version_flag_prints_version_and_exits(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    """`fglatch --version` should print the package version and exit cleanly."""
    monkeypatch.setattr("sys.argv", ["fglatch", "--version"])
    with pytest.raises(SystemExit) as exc_info:
        _main.run()
    assert exc_info.value.code == 0
    assert fglatch.__version__ in capsys.readouterr().out
