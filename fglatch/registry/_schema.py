from enum import StrEnum
from typing import TYPE_CHECKING

# TODO: switch to the public re-export once fgmetric promotes these symbols
# out of `_typing_extensions`.
from fgmetric._typing_extensions import TypeAnnotation
from latch.registry.table import Table
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import computed_field

if TYPE_CHECKING:
    from fglatch.registry._record_model import LatchRecordModel


class SchemaMismatchKind(StrEnum):
    """
    The category of a `SchemaMismatch`.

    Each value is the wire-stable string used in serialised mismatches.

    Members:
        MISSING_ON_TABLE: The model declares a field whose name does not appear as a
            column on the Registry table.
        MISSING_ON_MODEL: The Registry table has a column whose name is not declared
            as a field on the model. Only observed when the caller passes
            `allow_extra_columns=False`.
        TYPE_MISMATCH: The model field and the matching column both exist, but their
            unwrapped Python types disagree (e.g. model declares `str`, column is `int`).
        NULLABILITY_MISMATCH: The model field is declared with (or without) `| None`
            but the column's `allowEmpty` flag has the opposite value.
        ENUM_MEMBER_MISMATCH: The model field and the matching column are both enums,
            but their member sets disagree.
        BLOB_TYPE_MISMATCH: The model field is `LatchFile` (or `LatchDir`) and the
            column is a blob, but the SDK-resolved blob nodeType is the other variant.
    """

    MISSING_ON_TABLE = "missing_on_table"
    MISSING_ON_MODEL = "missing_on_model"
    TYPE_MISMATCH = "type_mismatch"
    NULLABILITY_MISMATCH = "nullability_mismatch"
    ENUM_MEMBER_MISMATCH = "enum_member_mismatch"
    BLOB_TYPE_MISMATCH = "blob_type_mismatch"


_MESSAGE_TEMPLATES: dict[SchemaMismatchKind, str] = {
    SchemaMismatchKind.MISSING_ON_TABLE: (
        "Field '{model_field}' ({model_type}) is declared on the model but the table has "
        "no matching column."
    ),
    SchemaMismatchKind.MISSING_ON_MODEL: (
        "Column '{column_name}' ({column_type}) exists on the table but is not declared "
        "on the model."
    ),
    SchemaMismatchKind.TYPE_MISMATCH: (
        "Field '{model_field}': model declared {model_type}, column is {column_type}."
    ),
    SchemaMismatchKind.NULLABILITY_MISMATCH: (
        "Field '{model_field}': model declared {model_type}, column is {column_type} "
        "(nullability disagrees)."
    ),
    SchemaMismatchKind.ENUM_MEMBER_MISMATCH: (
        "Field '{model_field}' enum members disagree: model declared {model_type}, "
        "column is {column_type}."
    ),
    SchemaMismatchKind.BLOB_TYPE_MISMATCH: (
        "Field '{model_field}': model declared {model_type}, but the column's blob "
        "nodeType resolves to {column_type}."
    ),
}
"""Per-kind format strings for `SchemaMismatch.message`. Templates reference only the
slots populated for their kind, so missing slots never appear in the rendered output."""


class SchemaMismatch(BaseModel):
    """
    A single mismatch between a `LatchRecordModel` field and its Registry column.

    Instances are frozen. The `model_*` slots describe the model side; the `column_*` slots
    describe the Registry side. Which slots are populated depends on `kind` (see field docs).

    The human-readable `message` is derived from the populated slots — callers should not
    construct messages by hand.

    Attributes:
        kind: Category of mismatch.
        model_field: Name of the offending field on the model. Populated for every kind
            except `missing_on_model`, where the model has no field by that name.
        column_name: Name of the offending column on the table. Populated for every kind
            except `missing_on_table`, where the table has no such column.
        model_type: The type annotation declared on the model. Populated for every kind
            except `missing_on_model`, where the model has no declaration.
        column_type: The Python type the SDK resolves the column to. Populated for every
            kind except `missing_on_table`, where the column does not exist.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    kind: SchemaMismatchKind
    model_field: str | None = None
    column_name: str | None = None
    model_type: TypeAnnotation | None = None
    column_type: TypeAnnotation | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def message(self) -> str:
        """Render a human-readable description from the populated slots."""
        return _MESSAGE_TEMPLATES[self.kind].format(
            model_field=self.model_field,
            column_name=self.column_name,
            model_type=_render(self.model_type),
            column_type=_render(self.column_type),
        )


class RegistryTableSchemaError(ValueError):
    """
    Raised when a `LatchRecordModel`'s declared schema does not match its Registry table.

    Subclasses `ValueError` so existing `except ValueError` callers continue to catch it.
    The per-field breakdown is available on `mismatches` for programmatic use.

    Attributes:
        mismatches: One `SchemaMismatch` per detected disagreement.
    """

    mismatches: list[SchemaMismatch]

    def __init__(self, mismatches: list[SchemaMismatch]) -> None:
        self.mismatches = mismatches
        summary = "\n".join(f"  - {m.message}" for m in mismatches)
        super().__init__(f"Registry table schema validation failed:\n{summary}")


def _render(annotation: TypeAnnotation | None) -> str:
    """
    Format a type annotation for inclusion in a `SchemaMismatch.message`.

    Bare `type` objects (e.g. `int`) render to their `__name__` to avoid `str(int)`'s
    ugly `"<class 'int'>"`. Everything else (PEP-604 unions, `list[T]`, etc.) renders
    via `str(...)`, which already returns the literal source-style annotation.
    """
    rendered: str
    if annotation is None:
        rendered = "<absent>"
    elif isinstance(annotation, type):
        rendered = annotation.__name__
    else:
        rendered = str(annotation)
    return rendered


_SKIPPED_MODEL_FIELDS: frozenset[str] = frozenset({"id", "name"})
"""Record metadata fields on `LatchRecordModel`; not Registry columns, so not validated."""


def _validate_table_schema(
    model_cls: "type[LatchRecordModel]",
    table: Table,
    *,
    allow_extra_columns: bool,
) -> list[SchemaMismatch]:
    """
    Compare a `LatchRecordModel`'s declared schema to a live Registry `Table`.

    Enumerates the model's `model_fields` (excluding the base `id` / `name`) and the
    table's columns; reports `missing_on_table` for declared fields with no matching
    column, and (when `allow_extra_columns=False`) `missing_on_model` for columns the
    model does not declare.

    Args:
        model_cls: A `LatchRecordModel` subclass whose `model_fields` are compared.
        table: A Registry table whose columns are inspected. The caller is responsible
            for calling `table.load()` before passing it in.
        allow_extra_columns: If False, columns present on the table but not declared on
            the model produce `missing_on_model` errors.

    Returns:
        One `SchemaMismatch` per detected disagreement. Empty list when the schema matches.
    """
    mismatches: list[SchemaMismatch] = []
    columns = table.get_columns()

    model_annotations: dict[str, TypeAnnotation] = {
        name: info.annotation
        for name, info in model_cls.model_fields.items()
        if name not in _SKIPPED_MODEL_FIELDS and info.annotation is not None
    }

    for field_name, annotation in model_annotations.items():
        if field_name not in columns:
            mismatches.append(
                SchemaMismatch(
                    kind=SchemaMismatchKind.MISSING_ON_TABLE,
                    model_field=field_name,
                    model_type=annotation,
                )
            )

    if not allow_extra_columns:
        for column_name, column in columns.items():
            if column_name in model_annotations:
                continue
            mismatches.append(
                SchemaMismatch(
                    kind=SchemaMismatchKind.MISSING_ON_MODEL,
                    column_name=column_name,
                    column_type=column.type,
                )
            )

    return mismatches
