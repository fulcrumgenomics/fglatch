from importlib.metadata import version

from fglatch._client.enums import ExecutionStatus
from fglatch._client.latch_client import LatchClient
from fglatch._client.models import Execution

__version__ = version("fglatch")

__all__ = [
    "LatchClient",
    "Execution",
    "ExecutionStatus",
]
