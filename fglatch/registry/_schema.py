from enum import Enum
from enum import StrEnum
from typing import TYPE_CHECKING
from typing import get_args

# TODO: switch to the public re-export once fgmetric promotes these symbols
# out of `_typing_extensions`.
from fgmetric._typing_extensions import TypeAnnotation
from fgmetric._typing_extensions import is_list
from fgmetric._typing_extensions import is_optional
from fgmetric._typing_extensions import unpack_optional
from latch.registry.record import Record
from latch.registry.table import Table
from latch.registry.types import Column
from latch.registry.utils import to_python_type
from latch.types.directory import LatchDir
from latch.types.file import LatchFile
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
            continue
        mismatch = _compare_field_to_column(field_name, annotation, columns[field_name])
        if mismatch is not None:
            mismatches.append(mismatch)

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


def _compare_field_to_column(
    field_name: str,
    model_annotation: TypeAnnotation,
    column: Column,
) -> SchemaMismatch | None:
    """
    Compare one model field's annotation to one Registry `Column`.

    Resolves nullability against the column's `allowEmpty` first; if the two
    disagree, returns a `NULLABILITY_MISMATCH`. Otherwise, unwraps `T | None`
    on both sides and defers the unwrapped comparison to `_compare_unwrapped`.

    The `allowEmpty` key is defensively defaulted to `False` ("required") if
    absent — the SDK's TypedDict marks it as required, but a malformed payload
    shouldn't crash validation.
    """
    model_is_nullable = is_optional(model_annotation)
    column_is_nullable: bool = column.upstream_type.get("allowEmpty", False)
    column_python_type = to_python_type(column.upstream_type["type"])

    if model_is_nullable != column_is_nullable:
        return SchemaMismatch(
            kind=SchemaMismatchKind.NULLABILITY_MISMATCH,
            model_field=field_name,
            column_name=column.key,
            model_type=model_annotation,
            column_type=column_python_type,
        )

    expected_type = unpack_optional(model_annotation) if model_is_nullable else model_annotation
    return _compare_unwrapped(field_name, column.key, expected_type, column_python_type)


def _compare_unwrapped(
    field_name: str,
    column_name: str,
    model_type: TypeAnnotation,
    column_type: TypeAnnotation,
) -> SchemaMismatch | None:
    """
    Compare a pre-unwrapped model type to a pre-unwrapped column type.

    Both inputs have already had `T | None` / `Union[T, EmptyCell]` stripped at the
    caller. Primitive identity and enum-member equality are the comparisons performed
    here; richer dispatch branches (blobs, arrays, links) are added by subsequent
    helpers in the same module.
    """
    # TODO: union columns (Latch `to_python_type` returns `Union[A, B]` for non-None
    # unions) fall through this dispatch and produce a confusing `TYPE_MISMATCH`. Add
    # explicit detection + a dedicated mismatch kind, or document the limitation.
    if isinstance(model_type, type) and issubclass(model_type, Enum):
        return _compare_enum(field_name, column_name, model_type, column_type)

    if model_type is LatchFile or model_type is LatchDir:
        return _compare_blob(field_name, column_name, model_type, column_type)

    if is_list(model_type):
        return _compare_list(field_name, column_name, model_type, column_type)

    if _looks_like_latch_record_model(model_type):
        return _compare_link(field_name, column_name, model_type, column_type)

    if model_type is column_type:
        return None

    return SchemaMismatch(
        kind=SchemaMismatchKind.TYPE_MISMATCH,
        model_field=field_name,
        column_name=column_name,
        model_type=model_type,
        column_type=column_type,
    )


def _compare_enum(
    field_name: str,
    column_name: str,
    model_enum: type[Enum],
    column_type: TypeAnnotation,
) -> SchemaMismatch | None:
    """
    Compare an `Enum` subclass on the model to a Registry `enum` column.

    The SDK builds the column's Python type via `Enum("Enum", members)`, which puts the
    Registry member strings in `.name` and auto-assigned ints in `.value`. Per Python
    convention, model enums put the Python identifier (e.g. "FOO") in `.name` and the
    user-assigned string (e.g. "Foo") in `.value`. The two sides that should agree are
    therefore **column member `.name`** ↔ **model member `.value`**.

    Model enums whose `.value` is not a string (e.g. `auto()`-valued, `IntEnum`) will
    fail this comparison wholesale because the value sets won't match the column's
    string members. That is intentional — the user must explicitly choose the Registry
    strings via `.value`.
    """
    if not (isinstance(column_type, type) and issubclass(column_type, Enum)):
        return SchemaMismatch(
            kind=SchemaMismatchKind.TYPE_MISMATCH,
            model_field=field_name,
            column_name=column_name,
            model_type=model_enum,
            column_type=column_type,
        )

    model_strings: set[str] = {m.value for m in model_enum if isinstance(m.value, str)}
    column_strings: set[str] = {m.name for m in column_type}

    if model_strings == column_strings:
        return None

    return SchemaMismatch(
        kind=SchemaMismatchKind.ENUM_MEMBER_MISMATCH,
        model_field=field_name,
        column_name=column_name,
        model_type=model_enum,
        column_type=column_type,
    )


def _compare_blob(
    field_name: str,
    column_name: str,
    model_type: TypeAnnotation,
    column_type: TypeAnnotation,
) -> SchemaMismatch | None:
    """
    Compare a `LatchFile` / `LatchDir` model field to a Registry `blob` column.

    The SDK's `to_python_type` routes blob columns through `get_blob_nodetype`, which
    returns `LatchDir` when `metadata.nodeType == "dir"` and `LatchFile` otherwise
    (including when metadata is missing entirely). So:

    - Column is not `LatchFile` / `LatchDir` → `TYPE_MISMATCH` (not a blob).
    - Sides agree → no mismatch.
    - Sides disagree on file-vs-dir → `BLOB_TYPE_MISMATCH`.
    """
    if column_type is not LatchFile and column_type is not LatchDir:
        return SchemaMismatch(
            kind=SchemaMismatchKind.TYPE_MISMATCH,
            model_field=field_name,
            column_name=column_name,
            model_type=model_type,
            column_type=column_type,
        )

    if model_type is column_type:
        return None

    return SchemaMismatch(
        kind=SchemaMismatchKind.BLOB_TYPE_MISMATCH,
        model_field=field_name,
        column_name=column_name,
        model_type=model_type,
        column_type=column_type,
    )


def _compare_list(
    field_name: str,
    column_name: str,
    model_type: TypeAnnotation,
    column_type: TypeAnnotation,
) -> SchemaMismatch | None:
    """
    Compare a `list[T]` model field to a Registry array column.

    Recurses on the element types through `_compare_unwrapped`, so any element kind
    (primitive, enum, blob, link, or nested list) is handled by the top-level dispatch.
    Element-level mismatches surface with the inner `kind` and a `[*]`-qualified
    `model_field`; e.g. `list[MyEnum]` against an `array<enum>` with disagreeing
    members produces an `ENUM_MEMBER_MISMATCH` with `model_field="xs[*]"`.

    Nullability is validated at the column boundary (`_compare_field_to_column`), not
    per-element — Registry arrays carry `allowEmpty` on the array itself.
    """
    if not is_list(column_type):
        return SchemaMismatch(
            kind=SchemaMismatchKind.TYPE_MISMATCH,
            model_field=field_name,
            column_name=column_name,
            model_type=model_type,
            column_type=column_type,
        )

    model_args = get_args(model_type)
    column_args = get_args(column_type)
    if len(model_args) != 1 or len(column_args) != 1:
        # Defensive: list[T] always has one arg via get_args, and to_python_type returns
        # List[T] with one arg. Falls through only on malformed inputs.
        return SchemaMismatch(
            kind=SchemaMismatchKind.TYPE_MISMATCH,
            model_field=field_name,
            column_name=column_name,
            model_type=model_type,
            column_type=column_type,
        )

    return _compare_unwrapped(
        f"{field_name}[*]", f"{column_name}[*]", model_args[0], column_args[0]
    )


def _looks_like_latch_record_model(t: TypeAnnotation) -> bool:
    """
    Structural check for a `LatchRecordModel` subclass.

    Identifies a Pydantic `BaseModel` subclass that declares the `id` and `name` fields
    `LatchRecordModel` mandates. The check is structural (no `issubclass(t, LatchRecordModel)`)
    so this module doesn't need to import `_record_model` — which imports back from here,
    and would otherwise produce a circular import that has to be papered over with a lazy
    function-local import.
    """
    return (
        isinstance(t, type) and issubclass(t, BaseModel) and {"id", "name"} <= t.model_fields.keys()
    )


def _compare_link(
    field_name: str,
    column_name: str,
    model_type: TypeAnnotation,
    column_type: TypeAnnotation,
) -> SchemaMismatch | None:
    """
    Confirm a `LatchRecordModel` model field maps to a Registry `link` column.

    Per spec, the link's target table is not checked — any `LatchRecordModel` subclass
    matches any `link` column. That keeps the check tolerant of models that represent
    only a subset of a larger linked table. The SDK's `to_python_type` maps link columns
    to `Record`, so a column is a link iff `column_type is Record`.
    """
    if column_type is Record:
        return None

    return SchemaMismatch(
        kind=SchemaMismatchKind.TYPE_MISMATCH,
        model_field=field_name,
        column_name=column_name,
        model_type=model_type,
        column_type=column_type,
    )
