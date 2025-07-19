from typing import Generator
from typing import ItemsView
from typing import KeysView
from typing import ValuesView

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

    def __getitem__(self, key: ExecutionIdAsString) -> Execution:
        """Get a single record by its mutation key."""
        return self.root[key]

    def __iter__(self) -> Generator[tuple[ExecutionIdAsString, Execution], None, None]:
        """Return an iterator over the execution IDs (as string) and executions."""
        yield from self.root.items()

    def keys(self) -> KeysView[ExecutionIdAsString]:
        """A view into the model's keys (execution IDs as string)."""
        return self.root.keys()

    def values(self) -> ValuesView[Execution]:
        """A view into the model's values (executions)."""
        return self.root.values()

    def items(self) -> ItemsView[ExecutionIdAsString, Execution]:
        """A view into the model's items (tuples of execution IDs as string and executions)."""
        return self.root.items()

    def __len__(self) -> int:
        """Return the number of retrieved executions."""
        return len(self.root)

    def __contains__(self, key: object) -> bool:
        """
        True if the model contains an execution with the given ID, False otherwise.

        The execution ID must be provided as a string.
        """
        return isinstance(key, ExecutionIdAsString) and key in self.root
