"""Constants used by the test suite."""

import os
from typing import Final

MOCK_TABLE_1_ID: str = "11730"
"""Identifier for mock-table-1."""

MOCK_RECORD_1_ID: str = "1128032177"
"""Identifier for mock_record_1 in mock-table-1."""

MOCK_RECORD_1_NAME: str = "DNA123/seq_abc"
"""Name for mock_record_1 in mock-table-1."""

MOCK_LINKED_RECORD_NAME: str = "POOL456/sample_xyz"
"""Name for a mocked linked record value."""

SCHEMA_FIXTURE_TABLE_ID: Final[str | None] = os.environ.get("FGLATCH_FIXTURE_TABLE_ID")
"""
ID of a Registry table used by the `test_schema_integration` integration tests.

Expected column layout is documented at the top of that test file. When this env var is
unset, the integration tests skip.
"""

SCHEMA_FIXTURE_LINKED_TABLE_ID: Final[str | None] = os.environ.get(
    "FGLATCH_FIXTURE_LINKED_TABLE_ID"
)
"""
ID of the companion Registry table pointed at by the fixture table's `link_col`. When this
env var is unset, the integration tests skip.
"""
