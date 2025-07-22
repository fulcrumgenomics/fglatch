from enum import StrEnum
from enum import unique


@unique
class ExecutionStatus(StrEnum):
    """
    The status of a workflow execution.

    Corresponds to the Literal union ExecutionStatus in Latch's SDK.
    https://github.com/latchbio/latch/blob/b071c749febae79451382536ec54ce238c7b7b41/src/latch_cli/services/launch/launch_v2.py#L29-L40
    """

    ABORTED = "ABORTED"
    ABORTING = "ABORTING"
    FAILED = "FAILED"
    QUEUED = "QUEUED"
    SUCCEEDED = "SUCCEEDED"
    RUNNING = "RUNNING"
    UNDEFINED = "UNDEFINED"
    INITIALIZING = "INITIALIZING"
    WAITING_FOR_RESOURCES = "WAITING_FOR_RESOURCES"
    SKIPPED = "SKIPPED"

    @property
    def is_terminal(self) -> bool:
        """
        True if the execution is in a terminal state.

        Terminal states are:
        - SUCCEEDED
        - ABORTED
        - FAILED
        - SKIPPED
        """
        return self in [
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.ABORTED,
            ExecutionStatus.FAILED,
            ExecutionStatus.SKIPPED,
        ]
