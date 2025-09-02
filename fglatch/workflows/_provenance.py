import os


def get_workflow_version() -> str:
    """
    Retrieve the workflow version.

    Active Latch executions include a set environment variable, `FLYTE_INTERNAL_TASK_VERSION`, which
    contains the current workflow version.

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
