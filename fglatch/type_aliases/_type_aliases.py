"""Type aliases used by package modules."""

from typing import TypeAlias

from pydantic import JsonValue

JsonDict: TypeAlias = dict[str, JsonValue]
"""A JSON dictionary."""

LatchWorkspaceId: TypeAlias = str
"""A Latch workspace ID, e.g. "1234"."""

ExecutionDisplayName: TypeAlias = str
"""An execution's display name, e.g. "Visitacion DV9-3-90"."""

ExecutionId: TypeAlias = int
"""An execution's ID, e.g. 123456."""

ExecutionIdAsString: TypeAlias = str
"""An execution's ID as a string, e.g. "123456"."""

S3Uri: TypeAlias = str
"""An S3 URI."""

LatchTimestamp: TypeAlias = str
"""
A date and time stamp with timezone.

Format: Mon, 27 Nov 2023 21:52:41 GMT
"""

WorkflowId: TypeAlias = int
"""A workflow's ID, e.g. 12345."""

WorkflowName: TypeAlias = str
"""A workflow's name, e.g. "RNA-Seq analysis"."""

WorkflowVersion: TypeAlias = str
"""
A workflow's version.

For Fulcrum-developed workflows, this is typically a SemVer-compliant version string, optionally
suffixed with `-dev`, and appended with one or more hashes (Latch and git commit).
"""

RecordName: TypeAlias = str
"""The name (primary key) of a Latch Registry record."""
