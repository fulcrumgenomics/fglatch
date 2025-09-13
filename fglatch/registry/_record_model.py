from typing import Any
from typing import ClassVar
from typing import Self

from latch.registry.record import Record
from latch.registry.table import Table
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

    Class Variables:
        table_id: The ID of the source Registry table. Must be defined in subclasses.
    """

    table_id: ClassVar[str]
    _table_name: ClassVar[str]

    id: str
    name: str

    @classmethod
    def __init_subclass__(cls, **kwargs: Any) -> None:
        table = Table(id=cls.table_id)
        cls._table_name = table.get_display_name()

    @classmethod
    def from_record(cls, record: Record) -> Self:
        """
        Create a validated model instance from a Latch Registry Record.

        Extracts values from the provided Record, adds the record's name and ID, and validates the
        data against the model schema.

        Args:
            record: A record retrieved from a Latch Registry table via the SDK.

        Returns:
            A validated instance of the model with all field data populated.

        Raises:
            ValueError: If the record originates from a different table than the one specified by
                this model's `table_id`.
            ValidationError: If the record data fails model validation (e.g. missing required
                fields, incorrect types).
        """
        if record.get_table_id() != cls.table_id:
            raise ValueError(
                f"Records must come from the table {cls._table_name} (id={cls.table_id})"
            )

        # Convert a Record to a dictionary.
        values: dict[str, Any] = record.get_values()

        # The record's name and ID are not included in the dictionary returned by
        # `Record.get_values()`, and they must be added manually.
        values["name"] = record.get_name()
        values["id"] = record.id

        return cls.model_validate(values)
