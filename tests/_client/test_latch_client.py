import pytest

from fglatch._client.enums import ExecutionStatus
from fglatch._client.latch_client import LatchClient
from fglatch._client.models import Execution
from fglatch.type_aliases import ExecutionIdAsString


@pytest.mark.requires_latch_api
def test_get_executions_online() -> None:
    """Test that we can retrieve executions from the Latch API."""
    client: LatchClient = LatchClient()
    executions: dict[ExecutionIdAsString, Execution] = client.get_executions()

    # `get_executions()` will always return _all_ of the executions in a workspace, so we can't test
    # the total number of executions, which will vary.
    # Instead, just check that a known (terminated) execution exists and has the right attributes
    # https://console.latch.bio/workflows/107206/executions/660236/results
    assert "660236" in executions

    execution: Execution = executions["660236"]
    assert execution.display_name == "Bernal HI50-40-0"
    assert execution.id == 660236
    assert (
        execution.inputs_url
        == "s3://prion-flyte-prod/metadata/19357/development/f75c559bcb9694c2fb89/inputs"
    )
    assert execution.resolution_time == "Sat, 15 Feb 2025 08:35:28 GMT"
    assert execution.start_time == "Sat, 15 Feb 2025 08:33:47 GMT"
    assert execution.status is ExecutionStatus.SUCCEEDED
    assert execution.workflow_id == 107206
    assert execution.workflow_name == "Fulcrum Genomics Sequencing Run Demultiplexer"
    assert execution.workflow_version == "1.0.0-f6b468-b809f7"
