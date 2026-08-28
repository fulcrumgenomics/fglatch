import pytest
from latch.registry.record import Record
from latch.registry.table import Table
from latch.registry.table import TableNotFoundError
from pydantic import ValidationError
from pytest_mock import MockerFixture

from fglatch.registry import LatchRecordModel
from fglatch.registry import query_latch_records_by_name
from fglatch.type_aliases import RecordName
from tests.constants import MOCK_TABLE_1_ID


@pytest.mark.requires_latch_registry
def test_query_latch_records_by_name_online() -> None:
    """query_latch_records_by_name() should fetch real data."""
    name: str = "mock_record_1"
    records: dict[RecordName, Record] = query_latch_records_by_name(name, table_id=MOCK_TABLE_1_ID)

    assert len(records) == 1
    assert name in records
    assert records[name].get_name() == name
    assert records[name].get_values().get("foo") == "hello"
    assert records[name].get_values().get("bar") == 42


@pytest.mark.requires_latch_registry
def test_query_latch_records_by_name_online_multiple_records() -> None:
    """query_latch_records_by_name() should fetch multiple records in one query."""
    names: list[str] = ["mock_record_1", "mock_record_2"]
    records: dict[RecordName, Record] = query_latch_records_by_name(names, table_id=MOCK_TABLE_1_ID)

    assert len(records) == 2
    for name in names:
        assert name in records
        assert records[name].get_name() == name

    assert records["mock_record_1"].get_values().get("foo") == "hello"
    assert records["mock_record_2"].get_values().get("foo") == "world"


@pytest.mark.requires_latch_registry
def test_query_latch_records_by_name_online_scopes_to_specified_table() -> None:
    """query_latch_records_by_name() should return the record from the specified table only."""
    # `duplicate_record_1` exists in both `mock-table-1` and `mock-table-2`.
    name: str = "duplicate_record_1"

    records = query_latch_records_by_name(name, table_id=MOCK_TABLE_1_ID)

    assert name in records
    assert records[name].get_values().get("foo") == "salutations"
    assert records[name].get_values().get("bar") == 7


@pytest.mark.requires_latch_registry
def test_query_latch_records_by_name_online_raises_if_no_record_with_specified_name() -> None:
    """query_latch_records_by_name() should raise for a name not present in the table."""
    name: str = "nonexistent"
    with pytest.raises(ValueError, match=f"No record found with name: {name}"):
        query_latch_records_by_name(name, table_id=MOCK_TABLE_1_ID)


@pytest.mark.requires_latch_registry
def test_query_latch_records_by_name_online_raises_if_table_not_found() -> None:
    """query_latch_records_by_name() should raise TableNotFoundError for a nonexistent table."""
    with pytest.raises(TableNotFoundError):
        query_latch_records_by_name("mock_record_1", table_id="999999999")


@pytest.mark.requires_latch_registry
def test_query_latch_records_by_name_online_primes_metadata_not_values(
    mocker: MockerFixture,
) -> None:
    """Name and table_id are primed (no load); values load lazily on first access."""
    load_spy = mocker.spy(Record, "load")

    records = query_latch_records_by_name("mock_record_1", table_id=MOCK_TABLE_1_ID)
    record = records["mock_record_1"]

    assert record.get_name() == "mock_record_1"
    assert record.get_table_id() == MOCK_TABLE_1_ID
    assert load_spy.call_count == 0  # name and table_id are primed — no round trip

    assert record.get_values().get("foo") == "hello"
    assert load_spy.call_count == 1  # values were not primed — loaded lazily


@pytest.mark.requires_latch_registry
def test_latch_record_model() -> None:
    """LatchRecordModel should validate real data."""
    name: str = "mock_record_1"
    records: dict[RecordName, Record] = query_latch_records_by_name(name, table_id=MOCK_TABLE_1_ID)

    assert len(records) == 1
    assert name in records

    validated_record = MockRecord.from_record(records[name])

    assert validated_record.name == name
    assert validated_record.foo == "hello"
    assert validated_record.bar == 42


FAKE_TABLE_ID: str = "FAKE_TABLE"


def _node(record_id: int, name: str) -> dict[str, object]:
    """A fake `catalogSamplesByExperimentId` node (ids are strings, as the API returns them)."""
    return {"id": str(record_id), "name": name}


def _response(*nodes: dict[str, object]) -> dict[str, object]:
    """A fake `execute()` response wrapping the given record nodes under one experiment."""
    return {
        "catalogExperiment": {
            "id": FAKE_TABLE_ID,
            "catalogSamplesByExperimentId": {"nodes": list(nodes)},
        }
    }


def test_query_latch_records_by_name_offline_primes_metadata_only(mocker: MockerFixture) -> None:
    """Records are keyed by name with name and table_id primed; values stay lazy (not primed)."""
    response = _response(_node(1, "name_1"), _node(2, "name_2"))
    mocker.patch("fglatch.registry._registry.execute", return_value=response)

    records = query_latch_records_by_name(["name_1", "name_2"], table_id=FAKE_TABLE_ID)

    assert set(records) == {"name_1", "name_2"}
    record_1 = records["name_1"]
    assert record_1.get_name(load_if_missing=False) == "name_1"
    assert record_1.get_table_id(load_if_missing=False) == FAKE_TABLE_ID
    assert record_1.get_values(load_if_missing=False) is None  # values are not primed


def test_query_latch_records_by_name_offline_scopes_query_to_table(mocker: MockerFixture) -> None:
    """The fetch is a single round trip scoped to the table and the requested names."""
    execute_mock = mocker.patch(
        "fglatch.registry._registry.execute", return_value=_response(_node(1, "r"))
    )

    query_latch_records_by_name("r", table_id=FAKE_TABLE_ID)

    execute_mock.assert_called_once()
    assert execute_mock.call_args.kwargs["variables"] == {
        "tableId": FAKE_TABLE_ID,
        "sampleNames": ["r"],
    }


def test_query_latch_records_by_name_offline_empty_input_returns_empty_without_query(
    mocker: MockerFixture,
) -> None:
    """An empty name list must return `{}` without issuing a query."""
    execute_mock = mocker.patch("fglatch.registry._registry.execute")

    assert query_latch_records_by_name([], table_id=FAKE_TABLE_ID) == {}
    execute_mock.assert_not_called()


def test_query_latch_records_by_name_offline_raises_if_table_not_found(
    mocker: MockerFixture,
) -> None:
    """A `null` experiment (nonexistent/forbidden table) must raise TableNotFoundError."""
    mocker.patch("fglatch.registry._registry.execute", return_value={"catalogExperiment": None})

    with pytest.raises(TableNotFoundError, match="Could not retrieve table id=NOPE"):
        query_latch_records_by_name("name_1", table_id="NOPE")


def test_query_latch_records_by_name_offline_raises_if_response_cannot_be_validated(
    mocker: MockerFixture,
) -> None:
    """A GQL response of the wrong shape must raise a ValidationError."""
    bad_response = {"catalogExperiment": {"whoops_whats_this": {"nodes": [{"id": 1, "name": "r"}]}}}
    mocker.patch("fglatch.registry._registry.execute", return_value=bad_response)

    with pytest.raises(ValidationError):
        query_latch_records_by_name("r", table_id=FAKE_TABLE_ID)


def test_query_latch_records_by_name_offline_raises_and_lists_all_missing_names(
    mocker: MockerFixture,
) -> None:
    """Every requested name absent from the response must appear in the raised error."""
    mocker.patch("fglatch.registry._registry.execute", return_value=_response(_node(1, "present")))

    with pytest.raises(ValueError) as excinfo:
        query_latch_records_by_name(["present", "missing_1", "missing_2"], table_id=FAKE_TABLE_ID)

    message = str(excinfo.value)
    assert "No record found with name: missing_1" in message
    assert "No record found with name: missing_2" in message


def test_query_latch_records_by_name_offline_raises_if_duplicate_name_in_table(
    mocker: MockerFixture,
) -> None:
    """Two records with the same name in one table must raise rather than silently overwrite."""
    response = _response(_node(1, "dup"), _node(2, "dup"))
    mocker.patch("fglatch.registry._registry.execute", return_value=response)

    with pytest.raises(ValueError, match="Multiple records named 'dup' found in table"):
        query_latch_records_by_name("dup", table_id=FAKE_TABLE_ID)


class MockRecord(LatchRecordModel):
    """
    A fake record for testing.

    Corresponds to `mock-table-1` (id=11730) in the Fulcrum workspace.
    """

    foo: str
    bar: int


def test_from_record_reads_lazy_values_offline(mocker: MockerFixture) -> None:
    """A record with unprimed values still validates: `from_record` lazy-loads them."""
    # The query primes only name/table_id — values are absent.
    mocker.patch("fglatch.registry._registry.execute", return_value=_response(_node(1, "r")))
    record = query_latch_records_by_name("r", table_id=FAKE_TABLE_ID)["r"]
    assert record.get_values(load_if_missing=False) is None

    # `from_record` -> `get_values()` triggers `Record.load()`; mock the SDK's record-level query.
    # This fixture is pinned to the SDK's `Record.load()` selection shape (record.py).
    full_sample = {
        "catalogSample": {
            "id": "1",
            "name": "r",
            "creationTime": "2024-01-01T00:00:00+00:00",
            "catalogEventsBySampleId": {"nodes": []},
            "catalogSampleColumnDataBySampleId": {
                "nodes": [
                    {"key": "foo", "data": {"value": "hello", "valid": True}},
                    {"key": "bar", "data": {"value": 42, "valid": True}},
                ]
            },
            "experiment": {
                "id": FAKE_TABLE_ID,
                "catalogExperimentColumnDefinitionsByExperimentId": {
                    "nodes": [
                        {
                            "key": "foo",
                            "type": {"type": {"primitive": "string"}, "allowEmpty": False},
                        },
                        {
                            "key": "bar",
                            "type": {"type": {"primitive": "integer"}, "allowEmpty": True},
                        },
                    ]
                },
            },
        }
    }
    mocker.patch("latch.registry.record.execute", return_value=full_sample)

    validated_record = MockRecord.from_record(record)

    assert validated_record.name == "r"
    assert validated_record.foo == "hello"
    assert validated_record.bar == 42


def test_from_record_raises_if_wrong_table_id(mocker: MockerFixture) -> None:
    """LatchRecordModel.from_record must be given a record from the same table."""
    expected_table_id = "1234"
    mock_table = mocker.MagicMock(spec=Table, id=expected_table_id)
    mock_table.get_display_name.return_value = "Expected Table"
    mocker.patch("fglatch.registry._record_model.Table", return_value=mock_table)

    mock_record = mocker.MagicMock(spec=Record, id="4505")
    mock_record.get_table_id.return_value = "567"

    with pytest.raises(ValueError, match="Records must come from the table Expected"):
        MockRecord.from_record(mock_record, expected_table_id)
