import os
from typing import Any
from unittest import mock

import pytest
from gql.transport.exceptions import TransportQueryError
from pytest_mock import MockerFixture

from fglatch.workflows import get_execution_name
from fglatch.workflows import get_workflow_version


@pytest.fixture
def fake_gql_response() -> dict[str, Any]:
    """Mock GQL response for execution metadata retrieval."""
    return {
        "executionCreatorByToken": {
            "flytedbId": "fake_flyte_ID",
            "info": {
                "displayName": "fake_execution_name",
            },
        }
    }


def test_get_execution_name(mocker: MockerFixture, fake_gql_response: dict[str, Any]) -> None:
    """Test the happy path."""
    mocker.patch("fglatch.workflows._provenance.execute", return_value=fake_gql_response)

    with mock.patch.dict(os.environ, {"FLYTE_INTERNAL_EXECUTION_ID": "fake_token"}, clear=True):
        execution_name = get_execution_name()

    assert execution_name == "fake_execution_name"


def test_get_execution_name_raises_if_flyte_internal_execution_id_is_unset() -> None:
    """Should raise if the required environment variable is unset."""
    with (
        mock.patch.dict(os.environ, clear=True),
        pytest.raises(ValueError, match="The environment variable FLYTE_INTERNAL_EXECUTION_ID"),
    ):
        get_execution_name()


def test_get_execution_name_raises_if_gql_query_fails(mocker: MockerFixture) -> None:
    """Should raise if the GQL query execution raises a TransportQueryError."""
    mocker.patch("fglatch.workflows._provenance.execute", side_effect=TransportQueryError("boom"))

    with (
        mock.patch.dict(os.environ, {"FLYTE_INTERNAL_EXECUTION_ID": "fake_token"}, clear=True),
        pytest.raises(ValueError, match="Could not retrieve execution name from Flyte execution"),
    ):
        get_execution_name()


def test_get_execution_name_raises_if_gql_response_cannot_be_validated(
    mocker: MockerFixture,
) -> None:
    """Should raise if the validation of the GQL query response raises a ValidationError."""
    mocker.patch("fglatch.workflows._provenance.execute", return_value={"bad": "data"})

    with (
        mock.patch.dict(os.environ, {"FLYTE_INTERNAL_EXECUTION_ID": "fake_token"}, clear=True),
        pytest.raises(ValueError, match="Could not retrieve execution name from Flyte execution"),
    ):
        get_execution_name()


def test_get_workflow_version() -> None:
    """Test the happy path."""
    with mock.patch.dict(os.environ, {"FLYTE_INTERNAL_TASK_VERSION": "fake_version"}, clear=True):
        workflow_version = get_workflow_version()

    assert workflow_version == "fake_version"


def test_get_workflow_version_should_raise_if_flyte_internal_task_version_is_unset() -> None:
    """Should raise if the required environment variable is unset."""
    with (
        mock.patch.dict(os.environ, clear=True),
        pytest.raises(ValueError, match="The environment variable FLYTE_INTERNAL_TASK_VERSION is "),
    ):
        get_workflow_version()
