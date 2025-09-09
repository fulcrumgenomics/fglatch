import warnings
from pathlib import Path
from typing import Final

import pytest
import requests
from latch.registry.table import Table
from latch_sdk_config.latch import config
from latch_sdk_config.user import user_config

from fglatch._constants import MOCK_TABLE_1_ID

FULCRUM_WORKSPACE_NAME: Final[str] = "Fulcrum Genomics"
"""The display name of the Fulcrum Genomics Latch workspace."""


def _latch_api_is_available() -> bool:
    """
    True if a network connection can be made to the Latch API.

    Online unit tests require authenticated access to the Fulcrum Genomics Latch workspace. If tests
    are run in an environment without an authenticated Latch user, or an environment where the
    active workspace is not set to Fulcrum Genomics, any tests requiring online Latch access will be
    marked as `XFAIL`.
    """
    if user_config.token == "" or user_config.workspace_id == "":
        # Require locally configured credentials and workspace for testing.
        # The official Latch functions check these attributes first, and then try to retrieve from
        # GQL or a login prompt. We don't want interactivity or network connections here.
        return False

    if user_config.workspace_name is None or user_config.workspace_name != FULCRUM_WORKSPACE_NAME:
        warnings.warn(
            "User is authenticated but the active workspace is not Fulcrum Genomics. "
            "Online unit tests will be xfailed.\n"
            "Try activating the Fulcrum Genomics workspace with `latch workspace`.",
            stacklevel=2,
        )
        return False

    try:
        resp = requests.post(
            url=config.api.user.list_workspaces,
            headers={"Authorization": f"Bearer {user_config.token}"},
            json={"ws_account_id": user_config.workspace_id},
        )

        resp.raise_for_status()

    except requests.HTTPError:
        return False

    return True


NO_LATCH_API_CONNECTION: bool = not _latch_api_is_available()


@pytest.fixture(autouse=True)
def check_latch_api_connection(request: pytest.FixtureRequest) -> None:
    """Skip tests that require a Latch API connection."""
    marker = request.node.get_closest_marker("requires_latch_api")

    if marker is not None and NO_LATCH_API_CONNECTION:
        pytest.xfail("Test requires Latch API connection to the Fulcrum Genomics workspace.")


def _latch_registry_is_available() -> bool:
    """True if a connection can be made to the Latch Registry via the Latch SDK."""
    try:
        table = Table(id=MOCK_TABLE_1_ID)
        table.get_columns()
        return True
    except Exception:
        return False


NO_LATCH_REGISTRY_CONNECTION: bool = not _latch_registry_is_available()


@pytest.fixture(autouse=True)
def check_latch_registry_connection(request: pytest.FixtureRequest) -> None:
    """Skip tests that require a Latch API connection."""
    marker = request.node.get_closest_marker("requires_latch_registry")

    if marker is not None and NO_LATCH_REGISTRY_CONNECTION:
        pytest.xfail("Test requires Latch SDK connection to the Fulcrum Genomics Latch Registry.")


@pytest.fixture(scope="session")
def datadir() -> Path:
    """Path to the test data directory."""
    return Path(__file__).parent / "data"
