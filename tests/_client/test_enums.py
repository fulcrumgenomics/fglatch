from typing import assert_never

import pytest

from fglatch._client.enums import ExecutionStatus


@pytest.mark.parametrize("status", ExecutionStatus)
def test_execution_status_is_terminal(status: ExecutionStatus) -> None:
    """Test that we can identify terminal statuses."""
    match status:
        case (
            ExecutionStatus.SUCCEEDED
            | ExecutionStatus.ABORTED
            | ExecutionStatus.FAILED
            | ExecutionStatus.SKIPPED
        ):
            assert status.is_terminal
        case (
            ExecutionStatus.RUNNING
            | ExecutionStatus.ABORTING
            | ExecutionStatus.QUEUED
            | ExecutionStatus.UNDEFINED
            | ExecutionStatus.WAITING_FOR_RESOURCES
            | ExecutionStatus.INITIALIZING
        ):
            assert not status.is_terminal
        case _:
            assert_never(status)
