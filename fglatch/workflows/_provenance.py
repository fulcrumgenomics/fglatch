import logging
import os
from typing import Any

import gql
from gql.transport.exceptions import TransportQueryError
from latch_sdk_gql.execute import execute
from pydantic import BaseModel
from pydantic import Field
from pydantic import ValidationError

logger = logging.getLogger(__name__)


class LatchExecutionInfo(BaseModel):
    """
    Metadata associated with a Latch workflow execution.

    The gql query below returns
    {'executionCreatorByToken': {'flytedbId': str, 'info': {'displayName': str}}}
    """

    display_name: str = Field(alias="displayName")


class LatchExecution(BaseModel):
    """
    A record of a Latch execution.

    The gql query below returns
    {'executionCreatorByToken': {'flytedbId': str, 'info': {'displayName': str}}}
    """

    flytedb_id: str = Field(alias="flytedbId")
    info: LatchExecutionInfo = Field(alias="info")


class ExecutionCreatorByTokenQueryResponse(BaseModel):
    """
    The body of a response to the GQL query for retrieving execution metadata.

    The gql query below returns
    {'executionCreatorByToken': {'flytedbId': str, 'info': {'displayName': str}}}
    """

    execution_creator_by_token: LatchExecution = Field(alias="executionCreatorByToken")


def get_execution_name() -> str:
    """
    Retrieve the current execution's name, as it appears in the Latch console.

    This function should only be used in the context of an active Latch execution. Active Latch
    executions include a set environment variable, `FLYTE_INTERNAL_EXECUTION_ID`, which is a key
    into Latch's GQL store. The execution's name, as it appears in the console, can be retrieved by
    querying GQL with this ID.

    Returns:
        The execution name.

    Raises:
        ValueError: If the environment variable `FLYTE_INTERNAL_EXECUTION_ID` is unset.
        ValueError: If the GQL query fails or its response cannot be parsed.
    """
    token: str | None = os.environ.get("FLYTE_INTERNAL_EXECUTION_ID")

    if token is None:
        raise ValueError(
            "The environment variable FLYTE_INTERNAL_EXECUTION_ID is unset. "
            "Are you sure the code is running inside a Latch workflow?"
        )

    try:
        data: dict[str, Any] = execute(
            gql.gql("""
            query executionCreatorsByToken($token: String!) {
                executionCreatorByToken(token: $token) {
                    flytedbId
                    info {
                        displayName
                    }
                }
            }
            """),
            {"token": token},
        )
        response = ExecutionCreatorByTokenQueryResponse.model_validate(data)

    except (TransportQueryError, ValidationError) as e:
        raise ValueError("Could not retrieve execution name from Flyte execution metadata.") from e

    execution_name: str = response.execution_creator_by_token.info.display_name

    return execution_name


def get_workflow_version() -> str:
    """
    Retrieve the workflow version of the current Latch execution.

    This function should only be used in the context of an active Latch execution. Active Latch
    executions include a set environment variable, `FLYTE_INTERNAL_TASK_VERSION`, which contains the
    current workflow version.

    Returns:
        The workflow version.

    Raises:
        ValueError: If the environment variable `FLYTE_INTERNAL_TASK_VERSION` is unset.
    """
    workflow_version: str | None = os.environ.get("FLYTE_INTERNAL_TASK_VERSION")

    if workflow_version is None:
        raise ValueError(
            "The environment variable FLYTE_INTERNAL_TASK_VERSION is unset. "
            "Are you sure the code is running inside a Latch workflow?"
        )

    return workflow_version
