import asyncio
import logging
from pathlib import Path
from typing import Final

import pytest
from latch.ldata.path import LPath
from latch_cli.services.launch import launch_v2
from pytest import LogCaptureFixture
from pytest_mock import MockerFixture

from fglatch import ExecutionStatus
from fglatch.tools.submit import _latchify_params
from fglatch.tools.submit import _wait_for_execution_completion
from fglatch.tools.submit import submit
from fglatch.type_aliases._type_aliases import JsonDict

FULCRUM_LATCH_HELLO_WORLD_WF_NAME: Final[str] = "wf.__init__.hello_world"
FULCRUM_LATCH_HELLO_WORLD_WF_VERSION: Final[str] = "0.1.0-dev-9cd9c9-ec88e2"


@pytest.fixture
def tim_parameter_json(datadir: Path) -> Path:
    """A parameter JSON to send Tim a friendly hello."""
    return datadir / "hello_tim.json"


@pytest.mark.requires_latch_api
def test_submit_from_params_online(caplog: LogCaptureFixture, tim_parameter_json: Path) -> None:
    """Online test of submission from parameter JSON."""
    caplog.set_level(logging.INFO)

    submit(
        wf_name=FULCRUM_LATCH_HELLO_WORLD_WF_NAME,
        wf_version=FULCRUM_LATCH_HELLO_WORLD_WF_VERSION,
        parameter_json=tim_parameter_json,
    )

    assert "Submitted workflow with execution ID:" in caplog.text


@pytest.mark.requires_latch_api
@pytest.mark.xfail
def test_submit_from_launch_plan_online() -> None:
    """Online test of submission from parameter JSON."""
    raise NotImplementedError("our LaunchPlans aren't registering and I'm not sure why")


def test_submit_from_params_offline(
    mocker: MockerFixture,
    caplog: LogCaptureFixture,
    tim_parameter_json: Path,
) -> None:
    """Mocked test of submission from parameter JSON."""
    caplog.set_level(logging.INFO)

    mock_execution = mocker.MagicMock(spec=launch_v2.Execution, id="123456")
    patch = mocker.patch("fglatch.tools.submit.launch_v2.launch", return_value=mock_execution)

    submit(
        wf_name=FULCRUM_LATCH_HELLO_WORLD_WF_NAME,
        wf_version=FULCRUM_LATCH_HELLO_WORLD_WF_VERSION,
        parameter_json=tim_parameter_json,
    )

    # Latchified contents of hello_tim.json
    expected_params = {
        "name": "Tim",
        "output_directory": LPath("latch:///fg_testing/hello_world/"),
    }

    patch.assert_called_once_with(
        wf_name=FULCRUM_LATCH_HELLO_WORLD_WF_NAME,
        version=FULCRUM_LATCH_HELLO_WORLD_WF_VERSION,
        params=expected_params,
    )

    assert "Submitted workflow with execution ID: 123456" in caplog.messages


def test_submit_from_launch_plan_offline(
    mocker: MockerFixture,
    caplog: LogCaptureFixture,
) -> None:
    """Mocked test of submission from launch plan name."""
    caplog.set_level(logging.INFO)

    mock_execution = mocker.MagicMock(spec=launch_v2.Execution, id="123456")
    patch = mocker.patch(
        "fglatch.tools.submit.launch_v2.launch_from_launch_plan",
        return_value=mock_execution,
    )

    submit(
        wf_name=FULCRUM_LATCH_HELLO_WORLD_WF_NAME,
        wf_version=FULCRUM_LATCH_HELLO_WORLD_WF_VERSION,
        launch_plan="Hello Nils",
    )

    patch.assert_called_once_with(
        wf_name=FULCRUM_LATCH_HELLO_WORLD_WF_NAME,
        version=FULCRUM_LATCH_HELLO_WORLD_WF_VERSION,
        lp_name="Hello Nils",
    )

    assert "Submitted workflow with execution ID: 123456" in caplog.messages


def test_submit_raises_if_both_launch_plan_and_parameter_json_are_specified(
    tim_parameter_json: Path,
) -> None:
    """Test mutually exclusive arguments."""
    with pytest.raises(ValueError, match="One and only one") as excinfo:
        submit(
            wf_name=FULCRUM_LATCH_HELLO_WORLD_WF_NAME,
            wf_version=FULCRUM_LATCH_HELLO_WORLD_WF_VERSION,
            launch_plan="Hello Nils",
            parameter_json=tim_parameter_json,
        )

    expected_msg = "One and only one of `--launch-plan` and `--parameter-json` must be specified."
    assert str(excinfo.value) == expected_msg


def test_submit_raises_if_neither_launch_plan_nor_parameter_json_are_specified() -> None:
    """Test mutually exclusive arguments."""
    with pytest.raises(ValueError, match="One and only one") as excinfo:
        submit(
            wf_name=FULCRUM_LATCH_HELLO_WORLD_WF_NAME,
            wf_version=FULCRUM_LATCH_HELLO_WORLD_WF_VERSION,
            launch_plan=None,
            parameter_json=None,
        )

    expected_msg = "One and only one of `--launch-plan` and `--parameter-json` must be specified."
    assert str(excinfo.value) == expected_msg


def test_latchify_params() -> None:
    """Test that we convert Latch URIs to LPath instances."""
    params: JsonDict = {
        "foo": 1,
        "bar": "two",
        "relative_local": "relative/local/path.txt",
        "absolute_local": "/absolute/local/path.txt",
        "s3_uri": "s3://path.txt",
        "latch_relative": "latch:///fg-testing/hello_world/hello.txt",
        "latch_with_account_root": "latch://1.account/fg-testing/hello_world/hello.txt",
    }

    latchified_params = _latchify_params(params)

    for key, value in latchified_params.items():
        if key in ["latch_relative", "latch_with_account_root"]:
            assert isinstance(value, LPath)
            assert value.path == params[key]
        else:
            assert value == params[key]


@pytest.mark.asyncio
async def test_wait_for_execution_completion_success(
    mocker: MockerFixture,
) -> None:
    """Test _wait_for_execution_completion with successful completion."""
    mock_execution = mocker.MagicMock(spec=launch_v2.Execution, id="test-exec-123")
    mock_completed_execution = mocker.MagicMock(
        spec=launch_v2.CompletedExecution,
        status="SUCCEEDED",
    )

    mock_execution.wait.return_value = mock_completed_execution

    completed_execution: launch_v2.CompletedExecution = await _wait_for_execution_completion(
        execution=mock_execution, timeout_minutes=5
    )

    assert ExecutionStatus(completed_execution.status) is ExecutionStatus.SUCCEEDED
    mock_execution.wait.assert_called_once()


@pytest.mark.asyncio
async def test_wait_for_execution_completion_timeout(
    mocker: MockerFixture,
    caplog: LogCaptureFixture,
) -> None:
    """Test _wait_for_execution_completion with timeout."""
    caplog.set_level(logging.ERROR)

    mock_execution = mocker.MagicMock(spec=launch_v2.Execution, id="test-exec-timeout")

    async def slow_wait() -> None:
        await asyncio.sleep(10)  # Simulate long-running execution
        return None

    mock_execution.wait.side_effect = slow_wait

    with pytest.raises(asyncio.TimeoutError):
        await _wait_for_execution_completion(
            execution=mock_execution,
            timeout_minutes=0.001,  # Very short timeout
        )

    assert "Workflow did not complete within 0.001 minutes" in caplog.text
    assert "Execution ID: test-exec-timeout" in caplog.text


def test_submit_with_wait_for_termination_success(
    mocker: MockerFixture,
    caplog: LogCaptureFixture,
    tim_parameter_json: Path,
) -> None:
    """Test submit() with wait_for_termination=True and successful completion."""
    caplog.set_level(logging.INFO)

    mock_execution = mocker.MagicMock(spec=launch_v2.Execution, id="success-exec")
    mocker.patch("fglatch.tools.submit.launch_v2.launch", return_value=mock_execution)

    mock_completed_execution = mocker.MagicMock(
        spec=launch_v2.CompletedExecution,
        status="SUCCEEDED",
    )
    mock_wait = mocker.patch(
        "fglatch.tools.submit._wait_for_execution_completion",
        return_value=mock_completed_execution,
    )
    mocker.patch("fglatch.tools.submit.asyncio.run", side_effect=lambda _: mock_wait.return_value)

    submit(
        wf_name=FULCRUM_LATCH_HELLO_WORLD_WF_NAME,
        wf_version=FULCRUM_LATCH_HELLO_WORLD_WF_VERSION,
        parameter_json=tim_parameter_json,
        wait_for_termination=True,
    )

    assert "Submitted workflow with execution ID: success-exec" in caplog.text
    assert "Workflow succeeded!" in caplog.text


def test_submit_with_wait_for_termination_failed(
    mocker: MockerFixture,
    caplog: LogCaptureFixture,
    tim_parameter_json: Path,
) -> None:
    """Test submit() with wait_for_termination=True and failed completion."""
    caplog.set_level(logging.INFO)

    mock_execution = mocker.MagicMock(spec=launch_v2.Execution, id="failed-exec")
    mocker.patch("fglatch.tools.submit.launch_v2.launch", return_value=mock_execution)

    mock_completed_execution = mocker.MagicMock(spec=launch_v2.CompletedExecution, status="FAILED")
    mock_wait = mocker.patch(
        "fglatch.tools.submit._wait_for_execution_completion",
        return_value=mock_completed_execution,
    )
    mocker.patch("fglatch.tools.submit.asyncio.run", side_effect=lambda _: mock_wait.return_value)

    mock_sys_exit = mocker.patch("fglatch.tools.submit.sys.exit")

    submit(
        wf_name=FULCRUM_LATCH_HELLO_WORLD_WF_NAME,
        wf_version=FULCRUM_LATCH_HELLO_WORLD_WF_VERSION,
        parameter_json=tim_parameter_json,
        wait_for_termination=True,
    )

    assert "Submitted workflow with execution ID: failed-exec" in caplog.text
    assert "Workflow completed with unsuccessful status: FAILED" in caplog.text
    mock_sys_exit.assert_called_once_with(1)
