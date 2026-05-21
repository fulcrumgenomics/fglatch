"""
Integration tests for `LatchRecordModel.validate_table_schema`.

These tests run against a live Registry table whose ID is read from the
`FGLATCH_FIXTURE_TABLE_ID` environment variable (and `FGLATCH_FIXTURE_LINKED_TABLE_ID`
for the companion table that `link_col` points at). They exist to confirm the SDK
shapes (`Table.get_columns()`, `Column.type`, `Column.upstream_type`) haven't drifted
out from under our validator. Per-kind error-branch coverage lives in
`test_schema.py` (unit tests with mocked Columns).

Fixture table column layout
---------------------------

The fixture table must be recreated in the Fulcrum Genomics Latch workspace if it
is lost or the env var is pointed elsewhere. Columns:

Primitives (`allowEmpty=False`):
- `string_col`   : string
- `int_col`      : integer
- `float_col`    : number
- `bool_col`     : boolean
- `date_col`     : date
- `datetime_col` : datetime

Nullable primitives (`allowEmpty=True`):
- `nullable_string_col` : string
- `nullable_int_col`    : integer

Blobs (both nodeType variants):
- `file_col` : blob (metadata.nodeType = file)
- `dir_col`  : blob (metadata.nodeType = dir)

Enum:
- `enum_col` : enum, members `["Alpha", "Beta", "Gamma"]` (identifier-mixed-case,
  so the assertion that the model's `.value` matches the column's `.name` actually
  exercises the comparison logic — same-case fixtures would let a name-on-both-sides
  regression slip through)

Arrays:
- `string_array_col`        : array<string>
- `nullable_int_array_col`  : array<integer>, allowEmpty

Link:
- `link_col` : link → the companion table referenced by
  `FGLATCH_FIXTURE_LINKED_TABLE_ID`.

The companion table needs no custom columns — the auto-provided Name column plus one
placeholder record is enough for `link_col` to point somewhere valid.
"""

from datetime import date
from datetime import datetime
from enum import Enum

import pytest
from latch.types.directory import LatchDir
from latch.types.file import LatchFile

from fglatch.registry import LatchRecordModel
from fglatch.registry import RegistryTableSchemaError
from fglatch.registry import SchemaMismatchKind
from tests.constants import SCHEMA_FIXTURE_TABLE_ID

pytestmark = [
    pytest.mark.requires_latch_registry,
    pytest.mark.skipif(
        SCHEMA_FIXTURE_TABLE_ID is None,
        reason="FGLATCH_FIXTURE_TABLE_ID env var is not set",
    ),
]


class _FixtureEnum(Enum):
    """
    Mirrors the fixture column's members with `.value` carrying the Registry string.

    Uses identifier-uppercase + value-mixed-case (`ALPHA = "Alpha"`) so a regression to
    name-on-both-sides comparison would fail this fixture — same-case pairs would let
    the bug slip through silently.
    """

    ALPHA = "Alpha"
    BETA = "Beta"
    GAMMA = "Gamma"


class FullFixtureModel(LatchRecordModel):
    """A 1:1 model mirroring the fixture table's column layout."""

    string_col: str
    int_col: int
    float_col: float
    bool_col: bool
    date_col: date
    datetime_col: datetime
    nullable_string_col: str | None = None
    nullable_int_col: int | None = None
    file_col: LatchFile
    dir_col: LatchDir
    enum_col: _FixtureEnum
    string_array_col: list[str]
    nullable_int_array_col: list[int] | None = None
    link_col: LatchRecordModel


class PrunedFixtureModel(LatchRecordModel):
    """Declares only a subset of the fixture table's columns."""

    string_col: str
    int_col: int


class ExtraFieldFixtureModel(LatchRecordModel):
    """Declares a field that the fixture table does not have."""

    string_col: str
    not_on_table: str


def test_full_model_matches_fixture_table() -> None:
    """A 1:1 model validates cleanly against the live fixture table."""
    assert SCHEMA_FIXTURE_TABLE_ID is not None
    FullFixtureModel.validate_table_schema(SCHEMA_FIXTURE_TABLE_ID)


def test_full_model_matches_fixture_table_strict() -> None:
    """The 1:1 model also validates with `allow_extra_columns=False`."""
    assert SCHEMA_FIXTURE_TABLE_ID is not None
    FullFixtureModel.validate_table_schema(SCHEMA_FIXTURE_TABLE_ID, allow_extra_columns=False)


def test_pruned_model_passes_when_extras_allowed() -> None:
    """A model declaring a subset of columns passes when `allow_extra_columns=True`."""
    assert SCHEMA_FIXTURE_TABLE_ID is not None
    PrunedFixtureModel.validate_table_schema(SCHEMA_FIXTURE_TABLE_ID)


def test_pruned_model_fails_when_extras_disallowed() -> None:
    """A pruned model fails with `missing_on_model` when `allow_extra_columns=False`."""
    assert SCHEMA_FIXTURE_TABLE_ID is not None
    with pytest.raises(RegistryTableSchemaError) as exc_info:
        PrunedFixtureModel.validate_table_schema(SCHEMA_FIXTURE_TABLE_ID, allow_extra_columns=False)
    kinds = {m.kind for m in exc_info.value.mismatches}
    assert kinds == {SchemaMismatchKind.MISSING_ON_MODEL}


def test_extra_field_on_model_always_fails() -> None:
    """A model with a field not on the table fails with `missing_on_table` regardless of flag."""
    assert SCHEMA_FIXTURE_TABLE_ID is not None

    for allow_extra in (True, False):
        with pytest.raises(RegistryTableSchemaError) as exc_info:
            ExtraFieldFixtureModel.validate_table_schema(
                SCHEMA_FIXTURE_TABLE_ID, allow_extra_columns=allow_extra
            )
        missing_on_table_fields = {
            m.model_field
            for m in exc_info.value.mismatches
            if m.kind is SchemaMismatchKind.MISSING_ON_TABLE
        }
        assert "not_on_table" in missing_on_table_fields
