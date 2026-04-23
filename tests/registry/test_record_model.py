import logging

import pytest
from latch.registry.record import Record
from latch.registry.table import Table
from latch.registry.table import TableNotFoundError
from latch.registry.types import EmptyCell
from latch.registry.types import InvalidValue
from pydantic import ValidationError
from pytest_mock import MockerFixture

from fglatch.registry import LatchRecordModel
from fglatch.registry import query_latch_records_by_name
from fglatch.registry._record_model import _safe_table_name
from fglatch.registry._record_model import _validate_source_table
from tests.constants import MOCK_RECORD_1_ID
from tests.constants import MOCK_TABLE_1_ID


def test_safe_table_name(mocker: MockerFixture) -> None:
    """Happy path."""
    mock_table = mocker.MagicMock(spec=Table)
    mock_table.get_display_name.return_value = "My Table"

    mocker.patch("fglatch.registry._record_model.Table", return_value=mock_table)

    assert _safe_table_name("1234") == "My Table"


def test_safe_table_name_raises(mocker: MockerFixture) -> None:
    """Should return None if the table doesn't exist or is inaccessible."""
    mock_table = mocker.MagicMock(spec=Table)
    mock_table.get_display_name.side_effect = TableNotFoundError

    mocker.patch("fglatch.registry._record_model.Table", side_effect=TableNotFoundError)

    assert _safe_table_name("1234") is None


@pytest.mark.requires_latch_registry
def test_validate_source_table_online() -> None:
    """Happy path."""
    record = Record(id=MOCK_RECORD_1_ID)

    _validate_source_table(record, MOCK_TABLE_1_ID)


def test_validate_source_table_offline(mocker: MockerFixture) -> None:
    """Happy path."""
    mock_record = mocker.MagicMock(spec=Record)
    mock_record.get_table_id.return_value = "1234"

    mock_table = mocker.MagicMock(spec=Table, id="1234")
    mock_table.get_display_name.return_value = "Expected Table"
    mocker.patch("fglatch.registry._record_model.Table", return_value=mock_table)

    _validate_source_table(mock_record, "1234")


def test_validate_source_table_raises_if_table_not_found(mocker: MockerFixture) -> None:
    """Should raise if the required table doesn't exist or is inaccessible."""
    mock_record = mocker.MagicMock(spec=Record)
    mocker.patch("fglatch.registry._record_model._safe_table_name", return_value=None)

    with pytest.raises(TableNotFoundError, match="Could not retrieve table id=567"):
        _validate_source_table(mock_record, "567")


def test_validate_source_table_raises_if_table_different(mocker: MockerFixture) -> None:
    """Should raise if the record came from a different table than specified."""
    mock_table_names = {"1234": "Actual Table", "567": "Expected Table"}
    mocker.patch(
        "fglatch.registry._record_model._safe_table_name",
        side_effect=lambda table_id: mock_table_names[table_id],
    )

    mock_record = mocker.MagicMock(spec=Record)
    mock_record.get_table_id.return_value = "1234"
    mock_record.id = "123"
    mock_record.get_name.return_value = "DNA123/seq_abc"

    with pytest.raises(ValueError, match="Records must come from the table") as excinfo:
        _validate_source_table(mock_record, "567")

    assert str(excinfo.value) == (
        "Records must come from the table Expected Table (id=567).\n"
        "Record DNA123/seq_abc (id=123) originated from table Actual Table (id=1234)."
    )


@pytest.mark.requires_latch_registry
def test_linked_record_online() -> None:
    """Test that we can load a linked record."""

    class Transcript(LatchRecordModel):
        """Represent a record from the Transcript table."""

        shortname: str
        gene: LatchRecordModel

    transcript_table_id = "12146"
    transcript_record_name = "ENST00000651671.1"

    records = query_latch_records_by_name(transcript_record_name, table_id=transcript_table_id)
    assert len(records) == 1

    record = records[transcript_record_name]
    transcript = Transcript.from_record(record, table_id=transcript_table_id)

    assert transcript.name == transcript_record_name
    assert transcript.shortname == "NOTCH1-204"
    assert isinstance(transcript.gene, LatchRecordModel)
    assert transcript.gene.name == "ENSG00000148400"


@pytest.mark.requires_latch_registry
def test_skip_linked_record_online() -> None:
    """Test that we can skip linked records not defined on the parent model."""

    class Transcript(LatchRecordModel):
        """Represent a record from the Transcript table."""

        shortname: str

    transcript_table_id = "12146"
    transcript_record_name = "ENST00000651671.1"

    records = query_latch_records_by_name(transcript_record_name, table_id=transcript_table_id)
    assert len(records) == 1

    record = records[transcript_record_name]
    transcript = Transcript.from_record(record, table_id=transcript_table_id)

    assert transcript.name == transcript_record_name
    assert transcript.shortname == "NOTCH1-204"


def test_from_record_exclude_empty_values(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    """EmptyCell values should be dropped and a warning logged when exclude_empty_values=True."""

    class SampleRecord(LatchRecordModel):
        sample_name: str
        concentration: float | None = None

    mock_record = mocker.MagicMock(spec=Record)
    mock_record.id = "42"
    mock_record.get_name.return_value = "sample_001"
    mock_record.get_values.return_value = {
        "sample_name": "my_sample",
        "concentration": mocker.MagicMock(spec=EmptyCell),
    }

    with caplog.at_level(logging.WARNING, logger="fglatch.registry._record_model"):
        result = SampleRecord.from_record(mock_record, exclude_empty_values=True)

    assert result.id == "42"
    assert result.name == "sample_001"
    assert result.sample_name == "my_sample"
    assert result.concentration is None

    expected_warning: str = "Empty cells found in record 'sample_001' for fields:\n\nconcentration"
    assert any(expected_warning in msg for msg in caplog.messages)


def test_from_record_exclude_invalid_values(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    """InvalidValue values should be dropped and a warning logged if exclude_invalid_values=True."""

    class SampleRecord(LatchRecordModel):
        sample_name: str
        concentration: float | None = None

    mock_invalid = mocker.MagicMock(spec=InvalidValue)
    mock_invalid.raw_value = "not_a_float"

    mock_record = mocker.MagicMock(spec=Record)
    mock_record.id = "42"
    mock_record.get_name.return_value = "sample_001"
    mock_record.get_values.return_value = {
        "sample_name": "my_sample",
        "concentration": mock_invalid,
    }

    with caplog.at_level(logging.WARNING, logger="fglatch.registry._record_model"):
        result = SampleRecord.from_record(mock_record, exclude_invalid_values=True)

    assert result.id == "42"
    assert result.name == "sample_001"
    assert result.sample_name == "my_sample"
    assert result.concentration is None

    expected_warning: str = (
        "Invalid values found in record 'sample_001' for fields:\n\nconcentration: not_a_float"
    )
    assert any(expected_warning in msg for msg in caplog.messages)


def test_from_record_exclude_invalid_values_and_empty_cells(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    """
    InvalidValue values should be dropped and a warning logged if exclude_invalid_values=True.

    EmptyCell values should be dropped and a warning logged when exclude_empty_values=True.
    """

    class SampleRecord(LatchRecordModel):
        sample_name: str
        concentration: float | None = None
        experiment_id: str | None = None

    mock_invalid = mocker.MagicMock(spec=InvalidValue)
    mock_invalid.raw_value = "not_a_float"

    mock_record = mocker.MagicMock(spec=Record)
    mock_record.id = "42"
    mock_record.get_name.return_value = "sample_001"
    mock_record.get_values.return_value = {
        "sample_name": "my_sample",
        "concentration": mock_invalid,
        "experiment_id": mocker.MagicMock(spec=EmptyCell),
    }

    with caplog.at_level(logging.WARNING, logger="fglatch.registry._record_model"):
        result = SampleRecord.from_record(
            mock_record, exclude_invalid_values=True, exclude_empty_values=True
        )

    assert result.id == "42"
    assert result.name == "sample_001"
    assert result.sample_name == "my_sample"
    assert result.concentration is None
    assert result.experiment_id is None

    expected_invalid_value_warning: str = (
        "Invalid values found in record 'sample_001' for fields:\n\nconcentration: not_a_float"
    )
    expected_empty_cell_warning: str = (
        "Empty cells found in record 'sample_001' for fields:\n\nexperiment_id"
    )
    assert any(expected_invalid_value_warning in msg for msg in caplog.messages)
    assert any(expected_empty_cell_warning in msg for msg in caplog.messages)


def test_from_record_invalid_value_raises_error(mocker: MockerFixture) -> None:
    """InvalidValue values should raise an error if exclude_invalid_values=False."""

    class SampleRecord(LatchRecordModel):
        sample_name: str
        concentration: float | None = None

    mock_invalid = mocker.MagicMock(spec=InvalidValue)
    mock_invalid.raw_value = "not_a_float"

    mock_record = mocker.MagicMock(spec=Record)
    mock_record.id = "42"
    mock_record.get_name.return_value = "sample_001"
    mock_record.get_values.return_value = {
        "sample_name": "my_sample",
        "concentration": mock_invalid,
    }

    with pytest.raises(
        ValidationError,
        match=(
            "1 validation error for SampleRecord\nconcentration\n  Input should be a valid number"
        ),
    ):
        SampleRecord.from_record(
            mock_record, exclude_invalid_values=False, exclude_empty_values=False
        )


def test_from_record_empty_value_raises_error(mocker: MockerFixture) -> None:
    """InvalidValue values should raise an error if exclude_empty_values=False."""

    class SampleRecord(LatchRecordModel):
        sample_name: str
        concentration: float | None = None

    mock_record = mocker.MagicMock(spec=Record)
    mock_record.id = "42"
    mock_record.get_name.return_value = "sample_001"
    mock_record.get_values.return_value = {
        "sample_name": "my_sample",
        "concentration": mocker.MagicMock(spec=EmptyCell),
    }

    with pytest.raises(
        ValidationError,
        match=(
            "1 validation error for SampleRecord\nconcentration\n  Input should be a valid number"
        ),
    ):
        SampleRecord.from_record(
            mock_record, exclude_invalid_values=False, exclude_empty_values=False
        )


@pytest.mark.requires_latch_registry
def test_from_record_exclude_empty_values_raises_online() -> None:
    """Removing an EmptyCell for a required field should raise a ValidationError."""

    class Transcript(LatchRecordModel):
        """Represent a record from the Transcript table."""

        shortname: str
        gene: LatchRecordModel

    transcript_table_id = "12146"
    transcript_record_name = "ENST00000123456.1"

    records = query_latch_records_by_name(transcript_record_name, table_id=transcript_table_id)
    assert len(records) == 1

    record = records[transcript_record_name]

    with pytest.raises(ValidationError):
        Transcript.from_record(record, table_id=transcript_table_id, exclude_empty_values=True)
