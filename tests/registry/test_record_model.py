import pytest
from latch.registry.record import Record
from latch.registry.table import Table
from latch.registry.table import TableNotFoundError
from pytest_mock import MockerFixture

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
