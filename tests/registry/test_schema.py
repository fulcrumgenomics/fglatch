import pytest

from fglatch.registry import RegistryTableSchemaError
from fglatch.registry import SchemaMismatch
from fglatch.registry import SchemaMismatchKind


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
