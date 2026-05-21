import logging
from typing import Any
from typing import Self

from latch.registry.record import Record
from latch.registry.table import Table
from latch.registry.table import TableNotFoundError
from latch.registry.types import EmptyCell
from latch.registry.types import InvalidValue
from pydantic import BaseModel

from fglatch.registry._schema import RegistryTableSchemaError
from fglatch.registry._schema import _validate_table_schema

logger = logging.getLogger(__name__)


class LatchRecordModel(BaseModel):
    """
    Base model for validating Latch Registry records.

    This model provides a schema definition and validation framework for Latch Registry table
    records retrieved via the SDK. Subclass this model to define table-specific field schemas and
    validation rules.

    The model automatically validates that records originate from the correct table and provides
    structured access to record data with type safety.

    Subclasses must define the `table_id` class variable to specify the source Registry table. The
    table's display name is automatically resolved from the table ID.

    Linked records are represented by a base `LatchRecordModel` instance containing only the `id`
    and `name` fields of the linked record.

    Examples:
        Define a model for a specific Registry table:

        >>> class SampleRecord(LatchRecordModel):
        ...     table_id = "11839"
        ...     sample_name: str
        ...     concentration: float

        Create and validate a record:

        >>> records = query_latch_records_by_name("sample_001")
        >>> validated_sample = SampleRecord.from_record(records["sample_001"])
        >>> print(validated_sample.sample_name)

    Attributes:
        id: The unique identifier of the record.
        name: The record's `Name` (primary key) in the Registry table.
    """

    id: str
    name: str

    @classmethod
    def from_record(
        cls,
        record: Record,
        table_id: str | None = None,
        exclude_empty_values: bool = False,
        exclude_invalid_values: bool = False,
        validate_schema: bool = False,
    ) -> Self:
        """
        Create a validated model instance from a Latch Registry Record.

        Extracts values from the provided Record, adds the record's name and ID, and validates the
        data against the model schema.

        Args:
            record: A record retrieved from a Latch Registry table via the SDK.
            table_id: An optional table ID to check the record against. Required when
                `validate_schema=True`.
            exclude_empty_values: If True, record attributes with value `EmptyCell` are excluded
                prior to validation and a warning is logged.
            exclude_invalid_values: If True, record attributes with value `InvalidValue` are
                excluded prior to validation and a warning is logged.
            validate_schema: If True, run `validate_table_schema(table_id)` before decoding the
                record's values. Fails loudly on schema drift. Requires `table_id` to be set.
                Each call performs a network round-trip to reload the table — callers iterating
                over many records should call `cls.validate_table_schema(table_id)` once up front
                and then call `from_record` without this flag.

        Returns:
            A validated instance of the model with all field data populated.

        Raises:
            ValueError: If the record originates from a different table than the one specified by
                `table_id`, or if `validate_schema=True` without a `table_id`.
            RegistryTableSchemaError: If `validate_schema=True` and the Registry table's schema
                disagrees with this model's schema.
            ValidationError: If the record data fails model validation (e.g. missing required
                fields, incorrect types).
        """
        if validate_schema and table_id is None:
            raise ValueError("`validate_schema=True` requires `table_id` to be set.")

        if table_id is not None:
            _validate_source_table(record, table_id)
            if validate_schema:
                cls.validate_table_schema(table_id)

        # Convert a Record to a dictionary.
        record_name: str = record.get_name()
        converted_values, invalid_values, empty_cells = _classify_record_values(record.get_values())

        if len(invalid_values) > 0:
            invalid_value_fields: str = "\n".join(
                f"{key}: {value}" for key, value in invalid_values.items()
            )
            logger.warning(
                f"Invalid values found in record '{record_name}' for fields:\n\n"
                f"{invalid_value_fields}"
            )

        if len(empty_cells) > 0:
            empty_cell_fields: str = "\n".join(empty_cells)
            logger.warning(
                f"Empty cells found in record '{record_name}' for fields:\n\n{empty_cell_fields}"
            )

        # Check for any InvalidValue or EmptyCell values, and make a copy of the key/value pairs,
        # excluding any that ought to be removed.
        out_values: dict[str, Any] = {
            key: value
            for key, value in converted_values.items()
            if not (exclude_invalid_values and key in invalid_values)
            and not (exclude_empty_values and key in empty_cells)
        }

        # The record's name and ID are not included in the dictionary returned by
        # `Record.get_values()`, and they must be added manually.
        out_values["name"] = record_name
        out_values["id"] = record.id

        return cls.model_validate(out_values)

    @classmethod
    def validate_table_schema(
        cls,
        table_id: str,
        *,
        allow_extra_columns: bool = True,
    ) -> None:
        """
        Validate that a Registry table's schema matches this model's declared schema.

        Fetches the table, compares its columns to the model's fields, and raises when
        they disagree. Intended to be called at startup / in CI to catch schema drift
        before data-level failures during `from_record`.

        Args:
            table_id: ID of the Registry table to validate against.
            allow_extra_columns: If True (default), columns on the table that are not
                declared on the model are silently accepted. If False, extra columns
                produce `MISSING_ON_MODEL` mismatches.

        Raises:
            RegistryTableSchemaError: If the table's schema disagrees with the model's
                schema. The exception's `mismatches` attribute carries one
                `SchemaMismatch` per detected disagreement.
        """
        # `Table(id=...)` is a lightweight handle; `.load()` populates its columns from
        # the Registry. The validator below calls `table.get_columns()`, which returns
        # the empty dict on an un-loaded table — so the network round-trip on `.load()`
        # is required for the validation to see anything.
        table = Table(id=table_id)
        table.load()
        mismatches = _validate_table_schema(cls, table, allow_extra_columns=allow_extra_columns)
        if mismatches:
            raise RegistryTableSchemaError(mismatches)


def _safe_table_name(table_id: str) -> str | None:
    """
    The display name of a given Registry table.

    Returns:
        The display name of the specified table. None if the table can't be loaded.
    """
    try:
        table = Table(id=table_id)
        return table.get_display_name()
    except TableNotFoundError:
        return None


def _validate_source_table(record: Record, table_id: str) -> None:
    """
    Validate the record came from the specified table.

    Raises:
        TableNotFoundError: If the table specified by `table_id` does not exist.
        ValueError: If `record` originated from a different table.
    """
    table_name: str | None = _safe_table_name(table_id)
    if table_name is None:
        raise TableNotFoundError(
            f"Could not retrieve table id={table_id}.\n"
            "Please check that the table ID is correct and that it exists in the active workspace."
        )

    record_table_id: str = record.get_table_id()
    if record_table_id != table_id:
        # NB: the string interpolation here is a little hacky. I think it's safe to assume that the
        # table from which the record originated still exists, so record_table_name is not None.
        # If that _isn't_ the case, I'd rather this error message just print `table None (id=<id>)`
        # instead of a more opaque message, or adding more detailed handling to the formatting of
        # this message.
        record_table_name: str | None = _safe_table_name(record_table_id)
        raise ValueError(
            f"Records must come from the table {table_name} (id={table_id}).\n"
            f"Record {record.get_name()} (id={record.id}) originated from "
            f"table {record_table_name} (id={record_table_id})."
        )


def _classify_record_values(
    values: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """
    Classify record values into converted values, invalid raw values, and empty-cell keys.

    Args:
      values: A dictionary mapping keys to values from a LatchRecord.

    Returns:
        A tuple of (converted, invalid, empty), where:
            - `converted` contains all record values, with linked `Record` instances replaced by
            base `LatchRecordModel` instances. `InvalidValue` and `EmptyCell` sentinels are
            preserved as-is so the caller can decide whether to exclude them.
            - `invalid` records the raw value for each `InvalidValue` encountered.
            - `empty` lists the keys that mapped to an `EmptyCell`.
    """
    converted: dict[str, Any] = {}
    invalid: dict[str, Any] = {}
    empty: list[str] = []

    for key, value in values.items():
        if isinstance(value, InvalidValue):
            invalid[key] = value.raw_value
            converted[key] = value
        elif isinstance(value, EmptyCell):
            empty.append(key)
            converted[key] = value
        elif isinstance(value, Record):
            converted[key] = LatchRecordModel(id=value.id, name=value.get_name())
        else:
            converted[key] = value

    return converted, invalid, empty
