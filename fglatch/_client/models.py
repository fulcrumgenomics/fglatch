from pydantic import BaseModel
from pydantic import Field
from pydantic import RootModel

from fglatch._client.enums import ExecutionStatus
from fglatch._shared.type_aliases import ExecutionDisplayName
from fglatch._shared.type_aliases import ExecutionId
from fglatch._shared.type_aliases import ExecutionIdAsString
from fglatch._shared.type_aliases import LatchTimestamp
from fglatch._shared.type_aliases import S3Uri
from fglatch._shared.type_aliases import WorkflowId
from fglatch._shared.type_aliases import WorkflowName
from fglatch._shared.type_aliases import WorkflowVersion


class Execution(BaseModel):
    """Execution metadata retrieved from the `get-executions` endpoint."""

    display_name: ExecutionDisplayName
    id: ExecutionId
    inputs_url: S3Uri | None = Field(default=None)
    resolution_time: LatchTimestamp | None = Field(default=None)
    start_time: LatchTimestamp | None = Field(default=None)
    status: ExecutionStatus
    workflow_id: WorkflowId
    workflow_name: WorkflowName
    workflow_version: WorkflowVersion


class ListedExecutions(RootModel[dict[ExecutionIdAsString, Execution]]):
    """The response of a POST request to the get-executions endpoint."""

    root: dict[ExecutionIdAsString, Execution]
