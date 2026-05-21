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
