import json
import logging
from pathlib import Path

from latch.ldata.path import LPath
from latch_cli.services.launch import launch_v2
from pydantic import JsonValue

from fglatch.type_aliases._type_aliases import JsonDict

logger = logging.getLogger(__name__)


def submit(
    *,
    wf_name: str,
    wf_version: str | None = None,
    launch_plan: str | None = None,
    parameter_json: Path | None = None,
) -> None:
    """
    Submit a workflow execution to Latch.

    This script submits a workflow execution to Latch using either test data from a registered
    LaunchPlan, or a custom set of parameters.

    Args:
        wf_name: The name of the Latch workflow to launch. Currently, this must be the name of the
            workflow as it appears in the workflow repository's `.latch/workflow_name`, e.g.
            "wf.__init__.hello_world".
        wf_version: The version of the Latch workflow to launch. If not provided, the latest
            registered version will be used.
        launch_plan: The name of a LaunchPlan registered to the workflow.
            This is mutually exclusive with `parameter_json`.
        parameter_json: A path to a JSON containing custom parameter mappings for the execution.
            This is mutually exclusive with `launch_plan`.

    Raises:
        ValueError: if neither or both of `--launch-plan` and `--parameter-json` are specified.
    """
    if (launch_plan is None) == (parameter_json is None):
        raise ValueError(
            "One and only one of `--launch-plan` and `--parameter-json` must be specified."
        )

    latch_execution: launch_v2.Execution
    if launch_plan is not None:
        latch_execution = launch_v2.launch_from_launch_plan(
            wf_name=wf_name,
            version=wf_version,
            lp_name=launch_plan,
        )
    else:
        # mypy can't follow that launch_plan and parameter_json are mutually exclusive, and at least
        # one is required, so we need the assertion for type narrowing
        assert parameter_json is not None

        with parameter_json.open() as jfile:
            params: JsonDict = json.load(jfile)

        latchified_params = _latchify_params(params)

        latch_execution = launch_v2.launch(
            wf_name=wf_name,
            version=wf_version,
            params=latchified_params,
        )

    logger.info(f"Submitted workflow with execution ID: {latch_execution.id}")


def _latchify_params(params: JsonDict) -> dict[str, JsonValue | LPath]:
    """
    Latchify parameter values parsed from a JSON file.

    Any Latch URIs (strings beginning with "latch://") are converted to `LPath` instances.

    Args:
        params: A dictionary loaded from a JSON file.

    Returns:
        A copy of the dictionary, with values modified as described above.
    """
    latchified_params: dict[str, JsonValue | LPath] = {}

    for key, value in params.items():
        if isinstance(value, str) and value.startswith("latch://"):
            latchified_params[key] = LPath(value)
        else:
            latchified_params[key] = value

    return latchified_params
