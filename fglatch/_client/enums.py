from enum import StrEnum
from enum import unique


@unique
class ExecutionStatus(StrEnum):
    """The status of a workflow execution."""

    ABORTED = "ABORTED"
    ABORTING = "ABORTING"
    FAILED = "FAILED"
    QUEUED = "QUEUED"
    SUCCEEDED = "SUCCEEDED"
    RUNNING = "RUNNING"
    UNDEFINED = "UNDEFINED"

    @property
    def is_terminal(self) -> bool:
        """
        True if the execution is in a terminal state.

        Terminal states are:
        - SUCCEEDED
        - ABORTED
        - FAILED
        """
        return self in [
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.ABORTED,
            ExecutionStatus.FAILED,
        ]
