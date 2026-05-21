from enum import Enum
from enum import IntEnum
from enum import StrEnum
from typing import Any
from typing import Literal
from typing import Union
from typing import cast

import pytest
from latch.registry.table import Table
from latch.registry.types import Column
from latch.registry.types import EmptyCell
from latch.registry.upstream_types.types import DBType
from pytest_mock import MockerFixture

from fglatch.registry import LatchRecordModel
from fglatch.registry import RegistryTableSchemaError
from fglatch.registry import SchemaMismatch
from fglatch.registry import SchemaMismatchKind
from fglatch.registry._schema import _validate_table_schema

_BasicPrimitive = Literal["string", "integer", "number", "boolean", "date", "datetime"]


def _column(py_type: Any, primitive: _BasicPrimitive, allow_empty: bool = False) -> Column:
    """Construct a realistic Column. The `key` is overwritten by `_table` below."""
    column_type: Any = Union[py_type, EmptyCell] if allow_empty else py_type
    upstream_type: DBType = {
        "type": {"primitive": primitive},
        "allowEmpty": allow_empty,
    }
    return Column(key="<placeholder>", type=column_type, upstream_type=upstream_type)


def _enum_column(members: list[str], allow_empty: bool = False) -> Column:
    """Build an enum Column mirroring the SDK's `Enum("Enum", members)` construction."""
    dynamic_enum: Any = Enum("Enum", members)  # type: ignore[misc]  # mypy can't infer dynamic enum members
    column_type: Any = Union[dynamic_enum, EmptyCell] if allow_empty else dynamic_enum
    upstream_type: DBType = {
        "type": {"primitive": "enum", "members": members},
        "allowEmpty": allow_empty,
    }
    return Column(key="<placeholder>", type=column_type, upstream_type=upstream_type)


def _table(mocker: MockerFixture, columns: dict[str, Column]) -> Table:
    """Build a mock Table whose `get_columns()` returns `columns` with matching keys."""
    keyed: dict[str, Column] = {
        name: Column(key=name, type=col.type, upstream_type=col.upstream_type)
        for name, col in columns.items()
    }
    table = mocker.MagicMock(spec=Table)
    table.get_columns.return_value = keyed
    return cast(Table, table)


def test_kind_str_enum_values() -> None:
    """Wire-stable string values — protects against accidental rename."""
    assert SchemaMismatchKind.MISSING_ON_TABLE.value == "missing_on_table"
    assert SchemaMismatchKind.MISSING_ON_MODEL.value == "missing_on_model"
    assert SchemaMismatchKind.TYPE_MISMATCH.value == "type_mismatch"
    assert SchemaMismatchKind.NULLABILITY_MISMATCH.value == "nullability_mismatch"
    assert SchemaMismatchKind.ENUM_MEMBER_MISMATCH.value == "enum_member_mismatch"
    assert SchemaMismatchKind.BLOB_TYPE_MISMATCH.value == "blob_type_mismatch"


def test_message_missing_on_table() -> None:
    """`missing_on_table` message names the model field and its declared type."""
    mismatch = SchemaMismatch(
        kind=SchemaMismatchKind.MISSING_ON_TABLE,
        model_field="x",
        model_type=str,
    )
    assert mismatch.message == (
        "Field 'x' (str) is declared on the model but the table has no matching column."
    )


def test_message_missing_on_model() -> None:
    """`missing_on_model` message names the column and its resolved type."""
    mismatch = SchemaMismatch(
        kind=SchemaMismatchKind.MISSING_ON_MODEL,
        column_name="x",
        column_type=int,
    )
    assert mismatch.message == (
        "Column 'x' (int) exists on the table but is not declared on the model."
    )


def test_message_type_mismatch() -> None:
    """`type_mismatch` message states both sides' types."""
    mismatch = SchemaMismatch(
        kind=SchemaMismatchKind.TYPE_MISMATCH,
        model_field="x",
        column_name="x",
        model_type=str,
        column_type=int,
    )
    assert mismatch.message == "Field 'x': model declared str, column is int."


def test_message_preserves_pep604_unions_literally() -> None:
    """No `typing.` rewriting — annotations render as their PEP 604 source form."""
    mismatch = SchemaMismatch(
        kind=SchemaMismatchKind.NULLABILITY_MISMATCH,
        model_field="x",
        column_name="x",
        model_type=str | None,
        column_type=str,
    )
    assert "str | None" in mismatch.message
    assert "str" in mismatch.message


def test_registry_table_schema_error_is_value_error() -> None:
    """Subclasses ValueError so existing `except ValueError` callers still catch it."""
    with pytest.raises(ValueError) as exc_info:
        raise RegistryTableSchemaError([])
    assert isinstance(exc_info.value, RegistryTableSchemaError)


def test_registry_table_schema_error_message_aggregates_mismatches() -> None:
    """`str(exc)` surfaces every underlying computed `message`."""
    mismatches = [
        SchemaMismatch(
            kind=SchemaMismatchKind.TYPE_MISMATCH,
            model_field="x",
            column_name="x",
            model_type=str,
            column_type=int,
        ),
        SchemaMismatch(
            kind=SchemaMismatchKind.MISSING_ON_TABLE,
            model_field="y",
            model_type=float,
        ),
    ]
    rendered = str(RegistryTableSchemaError(mismatches))
    assert "Field 'x': model declared str, column is int." in rendered
    assert (
        "Field 'y' (float) is declared on the model but the table has no matching column."
        in rendered
    )


# _validate_table_schema tests — enumeration (missing on each side).


def test_no_mismatches_when_dispatcher_finds_no_missing_fields(mocker: MockerFixture) -> None:
    """
    A model whose fields all exist on the table produces no enumeration mismatches.

    Per-field type comparison is added in a later commit, so this test does not yet
    require columns and model annotations to agree on type.
    """

    class Model(LatchRecordModel):
        x: str
        y: int

    table = _table(
        mocker,
        {"x": _column(str, "string"), "y": _column(int, "integer")},
    )

    assert _validate_table_schema(Model, table, allow_extra_columns=True) == []


def test_missing_on_table(mocker: MockerFixture) -> None:
    """A model field with no matching column produces `missing_on_table`."""

    class Model(LatchRecordModel):
        not_there: str

    mismatches = _validate_table_schema(Model, _table(mocker, {}), allow_extra_columns=True)

    assert len(mismatches) == 1
    assert mismatches[0].kind is SchemaMismatchKind.MISSING_ON_TABLE
    assert mismatches[0].model_field == "not_there"
    assert mismatches[0].model_type is str
    assert mismatches[0].column_name is None
    assert mismatches[0].column_type is None


def test_missing_on_model_silent_by_default(mocker: MockerFixture) -> None:
    """Columns the model doesn't declare are silent when `allow_extra_columns=True`."""

    class Model(LatchRecordModel):
        pass

    table = _table(mocker, {"extra_col": _column(str, "string")})

    assert _validate_table_schema(Model, table, allow_extra_columns=True) == []


def test_missing_on_model_when_strict(mocker: MockerFixture) -> None:
    """Columns the model doesn't declare error when `allow_extra_columns=False`."""

    class Model(LatchRecordModel):
        pass

    table = _table(mocker, {"extra_col": _column(str, "string")})

    mismatches = _validate_table_schema(Model, table, allow_extra_columns=False)

    assert len(mismatches) == 1
    assert mismatches[0].kind is SchemaMismatchKind.MISSING_ON_MODEL
    assert mismatches[0].column_name == "extra_col"
    assert mismatches[0].column_type is str
    assert mismatches[0].model_field is None
    assert mismatches[0].model_type is None


def test_id_and_name_are_skipped(mocker: MockerFixture) -> None:
    """The base model's `id` and `name` are not validated against table columns."""

    class Model(LatchRecordModel):
        x: str

    # Table has no `id` / `name` columns, yet the model validates cleanly.
    assert (
        _validate_table_schema(
            Model,
            _table(mocker, {"x": _column(str, "string")}),
            allow_extra_columns=True,
        )
        == []
    )


def test_multiple_mismatches_collected(mocker: MockerFixture) -> None:
    """Multiple disagreements surface together, not one-at-a-time."""

    class Model(LatchRecordModel):
        on_model_only: str

    table = _table(mocker, {"on_table_only": _column(int, "integer")})

    mismatches = _validate_table_schema(Model, table, allow_extra_columns=False)

    kinds = {m.kind for m in mismatches}
    assert kinds == {SchemaMismatchKind.MISSING_ON_TABLE, SchemaMismatchKind.MISSING_ON_MODEL}


# _validate_table_schema tests — primitive comparison and nullability.


def test_validate_all_primitives_happy(mocker: MockerFixture) -> None:
    """A model with each supported primitive matches a correspondingly-typed table."""
    from datetime import date
    from datetime import datetime

    class Model(LatchRecordModel):
        string_col: str
        int_col: int
        float_col: float
        bool_col: bool
        date_col: date
        datetime_col: datetime

    table = _table(
        mocker,
        {
            "string_col": _column(str, "string"),
            "int_col": _column(int, "integer"),
            "float_col": _column(float, "number"),
            "bool_col": _column(bool, "boolean"),
            "date_col": _column(date, "date"),
            "datetime_col": _column(datetime, "datetime"),
        },
    )

    assert _validate_table_schema(Model, table, allow_extra_columns=True) == []


def test_type_mismatch_emits_type_mismatch(mocker: MockerFixture) -> None:
    """Model field type differs from the column's primitive → `TYPE_MISMATCH`."""

    class Model(LatchRecordModel):
        x: str

    mismatches = _validate_table_schema(
        Model,
        _table(mocker, {"x": _column(int, "integer")}),
        allow_extra_columns=True,
    )

    assert len(mismatches) == 1
    assert mismatches[0].kind is SchemaMismatchKind.TYPE_MISMATCH
    assert mismatches[0].model_field == "x"
    assert mismatches[0].column_name == "x"
    assert mismatches[0].model_type is str
    assert mismatches[0].column_type is int


def test_nullable_field_matches_allow_empty_column(mocker: MockerFixture) -> None:
    """`T | None` on the model matches `allowEmpty=True` on the column."""

    class Model(LatchRecordModel):
        maybe_str: str | None = None

    table = _table(mocker, {"maybe_str": _column(str, "string", allow_empty=True)})

    assert _validate_table_schema(Model, table, allow_extra_columns=True) == []


def test_nullability_mismatch_model_nullable_column_required(mocker: MockerFixture) -> None:
    """Model declares `T | None` but the column is required → `NULLABILITY_MISMATCH`."""

    class Model(LatchRecordModel):
        x: str | None = None

    mismatches = _validate_table_schema(
        Model,
        _table(mocker, {"x": _column(str, "string", allow_empty=False)}),
        allow_extra_columns=True,
    )

    assert len(mismatches) == 1
    assert mismatches[0].kind is SchemaMismatchKind.NULLABILITY_MISMATCH


def test_nullability_mismatch_model_required_column_nullable(mocker: MockerFixture) -> None:
    """Model declares `T` but the column is nullable → `NULLABILITY_MISMATCH`."""

    class Model(LatchRecordModel):
        x: str

    mismatches = _validate_table_schema(
        Model,
        _table(mocker, {"x": _column(str, "string", allow_empty=True)}),
        allow_extra_columns=True,
    )

    assert len(mismatches) == 1
    assert mismatches[0].kind is SchemaMismatchKind.NULLABILITY_MISMATCH


def test_missing_allow_empty_defaults_to_required(mocker: MockerFixture) -> None:
    """A column whose upstream_type lacks `allowEmpty` is treated as required (defensive)."""

    class Model(LatchRecordModel):
        x: str

    upstream_type: DBType = {"type": {"primitive": "string"}}  # type: ignore[typeddict-item]  # intentionally omits allowEmpty
    column = Column(key="x", type=str, upstream_type=upstream_type)

    mismatches = _validate_table_schema(
        Model,
        _table(mocker, {"x": column}),
        allow_extra_columns=True,
    )

    # Model is required (no `| None`); column treated as required → no mismatch.
    assert mismatches == []


# Enum tests.
#
# The SDK builds column enums via `Enum("Enum", members)`, which puts the Registry strings
# in `.name` and auto-ints in `.value`. Model enums put the Python identifier in `.name`
# and the user-assigned string in `.value`. So the comparison is model `.value` ↔ column
# `.name`. These fixtures exercise the realistic shape where identifiers and values differ.


class _Status(StrEnum):
    """StrEnum: `.value` is the Registry string we want to match against."""

    ALPHA = "Alpha"
    BETA = "Beta"
    GAMMA = "Gamma"


class _StatusMissingMember(StrEnum):
    """Missing GAMMA relative to the fixture column."""

    ALPHA = "Alpha"
    BETA = "Beta"


class _StatusExtraMember(StrEnum):
    """Declares DELTA, which the fixture column does not have."""

    ALPHA = "Alpha"
    BETA = "Beta"
    GAMMA = "Gamma"
    DELTA = "Delta"


class _StatusNonStringValues(Enum):
    """Non-string-valued enum — values are ints, so model `.value` can't match column `.name`."""

    ALPHA = 1
    BETA = 2
    GAMMA = 3


def test_enum_field_happy(mocker: MockerFixture) -> None:
    """A model StrEnum whose values match the column's members validates cleanly."""

    class Model(LatchRecordModel):
        status: _Status

    table = _table(mocker, {"status": _enum_column(["Alpha", "Beta", "Gamma"])})

    assert _validate_table_schema(Model, table, allow_extra_columns=True) == []


def test_enum_member_missing_on_model(mocker: MockerFixture) -> None:
    """Column has a member the model does not → `ENUM_MEMBER_MISMATCH`."""

    class Model(LatchRecordModel):
        status: _StatusMissingMember

    table = _table(mocker, {"status": _enum_column(["Alpha", "Beta", "Gamma"])})

    mismatches = _validate_table_schema(Model, table, allow_extra_columns=True)

    assert len(mismatches) == 1
    assert mismatches[0].kind is SchemaMismatchKind.ENUM_MEMBER_MISMATCH


def test_enum_member_extra_on_model(mocker: MockerFixture) -> None:
    """Model declares a member the column does not → `ENUM_MEMBER_MISMATCH`."""

    class Model(LatchRecordModel):
        status: _StatusExtraMember

    table = _table(mocker, {"status": _enum_column(["Alpha", "Beta", "Gamma"])})

    mismatches = _validate_table_schema(Model, table, allow_extra_columns=True)

    assert len(mismatches) == 1
    assert mismatches[0].kind is SchemaMismatchKind.ENUM_MEMBER_MISMATCH


def test_enum_with_non_string_values_fails(mocker: MockerFixture) -> None:
    """Model enum with non-string values can't match the column's string members."""

    class Model(LatchRecordModel):
        status: _StatusNonStringValues  # values are ints, not strings

    table = _table(mocker, {"status": _enum_column(["Alpha", "Beta", "Gamma"])})

    mismatches = _validate_table_schema(Model, table, allow_extra_columns=True)

    assert len(mismatches) == 1
    assert mismatches[0].kind is SchemaMismatchKind.ENUM_MEMBER_MISMATCH


def test_enum_int_enum_member_mismatch(mocker: MockerFixture) -> None:
    """`IntEnum` model field can never match a column whose members are Registry strings."""

    class IntStatus(IntEnum):
        ALPHA = 1
        BETA = 2

    class Model(LatchRecordModel):
        status: IntStatus

    table = _table(mocker, {"status": _enum_column(["Alpha", "Beta"])})

    mismatches = _validate_table_schema(Model, table, allow_extra_columns=True)

    assert len(mismatches) == 1
    assert mismatches[0].kind is SchemaMismatchKind.ENUM_MEMBER_MISMATCH


def test_enum_field_type_mismatch_when_column_is_not_enum(mocker: MockerFixture) -> None:
    """Model declares an Enum but the column is a string primitive → `TYPE_MISMATCH`."""

    class Model(LatchRecordModel):
        status: _Status

    table = _table(mocker, {"status": _column(str, "string")})

    mismatches = _validate_table_schema(Model, table, allow_extra_columns=True)

    assert len(mismatches) == 1
    assert mismatches[0].kind is SchemaMismatchKind.TYPE_MISMATCH


def test_nullable_enum_field_happy(mocker: MockerFixture) -> None:
    """`StrEnumT | None` on the model ↔ nullable enum column validates cleanly."""

    class Model(LatchRecordModel):
        status: _Status | None = None

    table = _table(
        mocker,
        {"status": _enum_column(["Alpha", "Beta", "Gamma"], allow_empty=True)},
    )

    assert _validate_table_schema(Model, table, allow_extra_columns=True) == []
