from typing import Any
from typing import Self

from latch.registry.record import Record
from latch.registry.table import Table
from latch.registry.table import TableNotFoundError
from pydantic import BaseModel


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
    def from_record(cls, record: Record, table_id: str | None = None) -> Self:
        """
        Create a validated model instance from a Latch Registry Record.

        Extracts values from the provided Record, adds the record's name and ID, and validates the
        data against the model schema.

        Args:
            record: A record retrieved from a Latch Registry table via the SDK.
            table_id: An optional table ID to check the record against.

        Returns:
            A validated instance of the model with all field data populated.

        Raises:
            ValueError: If the record originates from a different table than the one specified by
                `table_id`.
            ValidationError: If the record data fails model validation (e.g. missing required
                fields, incorrect types).
        """
        if table_id is not None:
            _validate_source_table(record, table_id)

        # Convert a Record to a dictionary.
        values: dict[str, Any] = record.get_values()

        # The record's name and ID are not included in the dictionary returned by
        # `Record.get_values()`, and they must be added manually.
        values["name"] = record.get_name()
        values["id"] = record.id

        return cls.model_validate(values)


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
