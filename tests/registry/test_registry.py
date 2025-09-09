from typing import Any

import pytest
from latch.registry.record import Record
from pydantic import ValidationError
from pytest_mock import MockerFixture

from fglatch._constants import MOCK_TABLE_1_ID
from fglatch.registry import query_latch_records_by_name
from fglatch.type_aliases import RecordName


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
    """query_latch_records_by_name() should fetch real data."""
    names: list[str] = ["mock_record_1", "mock_record_2"]
    records: dict[RecordName, Record] = query_latch_records_by_name(names, table_id=MOCK_TABLE_1_ID)

    assert len(records) == 2

    for name in names:
        assert name in records
        assert records[name].get_name() == name

    assert records["mock_record_1"].get_values().get("foo") == "hello"
    assert records["mock_record_2"].get_values().get("foo") == "world"


@pytest.mark.requires_latch_registry
def test_query_latch_records_by_name_online_gets_record_from_specified_table() -> None:
    """query_latch_records_by_name() should fetch real data."""
    # There should be one record in `fglatch-tests / mock-table-1` and one record in
    # `fglatch-tests / mock-table-2`
    name: str = "duplicate_record_1"

    records = query_latch_records_by_name(name, table_id=MOCK_TABLE_1_ID)

    assert name in records
    assert records[name].get_values().get("foo") == "salutations"
    assert records[name].get_values().get("bar") == 7


@pytest.mark.requires_latch_registry
def test_query_latch_records_by_name_online_raises_if_no_record_with_specified_name() -> None:
    """query_latch_records_by_name() should fetch real data."""
    name: str = "nonexistent"
    with pytest.raises(ValueError) as excinfo:
        query_latch_records_by_name(name, table_id=MOCK_TABLE_1_ID)

    assert f"No record found with name: {name}" in str(excinfo.value)


@pytest.fixture
def fake_gql_response() -> dict[str, Any]:
    """Fake response from the GQL query used in mocked/offline tests."""
    return {
        "catalogSamples": {
            "nodes": [
                {"id": 1},
                {"id": 2},
            ]
        }
    }


def test_query_latch_records_by_name_offline(
    mocker: MockerFixture,
    fake_gql_response: dict[str, Any],
) -> None:
    """query_latch_records_by_name() should fetch mocked data."""
    mocker.patch("fglatch.registry._registry.execute", return_value=fake_gql_response)

    fake_table_id = "FAKE_TABLE"

    mock_record_1 = mocker.MagicMock(spec=Record)
    mock_record_1.get_name.return_value = "name_1"
    mock_record_1.get_table_id.return_value = fake_table_id

    mock_record_2 = mocker.MagicMock(spec=Record)
    mock_record_2.get_name.return_value = "name_2"
    mock_record_2.get_table_id.return_value = fake_table_id

    mock_records = {
        "1": mock_record_1,
        "2": mock_record_2,
    }

    mocker.patch(
        "fglatch.registry._registry.Record",
        side_effect=lambda node_id: mock_records[node_id],
    )

    records: dict[RecordName, Record] = query_latch_records_by_name(
        ["name_1", "name_2"],
        table_id=fake_table_id,
    )

    assert len(records) == 2
    assert "name_1" in records and "name_2" in records
    assert records["name_1"] == mock_record_1
    assert records["name_2"] == mock_record_2


def test_query_latch_records_by_name_raises_if_no_record_returned_by_gql(
    mocker: MockerFixture,
) -> None:
    """Should raise a ValueError if a Record isn't returned for one of the requested names."""
    bad_response = {
        "catalogSamples": {
            "nodes": [
                {"id": 1},
            ]
        }
    }
    mocker.patch("fglatch.registry._registry.execute", return_value=bad_response)

    mock_record_1 = mocker.MagicMock(spec=Record)
    mock_record_1.get_name.return_value = "name_1"
    mock_record_1.get_table_id.return_value = "FAKE_TABLE"

    mock_records = {
        "1": mock_record_1,
    }

    mocker.patch(
        "fglatch.registry._registry.Record",
        side_effect=lambda node_id: mock_records[node_id],
    )

    with pytest.raises(ValueError) as excinfo:
        query_latch_records_by_name(["name_1", "name_2"], table_id="FAKE_TABLE")

    assert "No record found with name: name_2" in str(excinfo.value)


def test_query_latch_records_by_name_raises_if_duplicate_records_returned_by_gql(
    mocker: MockerFixture,
) -> None:
    """Should raise a ValueError if multiple records are returned with the same name."""
    bad_response = {
        "catalogSamples": {
            "nodes": [
                {"id": 1},
                {"id": 2},
                {"id": 3},
            ]
        }
    }
    mocker.patch("fglatch.registry._registry.execute", return_value=bad_response)

    fake_table_id = "FAKE_TABLE"

    mock_record_1 = mocker.MagicMock(spec=Record)
    mock_record_1.get_name.return_value = "name_1"
    mock_record_1.get_table_id.return_value = fake_table_id

    mock_record_2 = mocker.MagicMock(spec=Record)
    mock_record_2.get_name.return_value = "name_2"
    mock_record_2.get_table_id.return_value = fake_table_id

    mock_record_3 = mocker.MagicMock(spec=Record)
    mock_record_3.get_name.return_value = "name_1"  # Deliberate, this creates the collision
    mock_record_3.get_table_id.return_value = fake_table_id

    mock_records = {
        "1": mock_record_1,
        "2": mock_record_2,
        "3": mock_record_3,
    }

    mocker.patch(
        "fglatch.registry._registry.Record",
        side_effect=lambda node_id: mock_records[node_id],
    )

    with pytest.raises(ValueError) as excinfo:
        query_latch_records_by_name(["name_1", "name_2"], table_id=fake_table_id)

    assert "Duplicate record name: name_1 (n=2)" in str(excinfo.value)


def test_query_latch_records_by_name_raises_if_response_cannot_be_validated(
    mocker: MockerFixture,
) -> None:
    """Should raise a ValueError if the GQL response can't be validated."""
    bad_response = {
        "catalogSamples": {
            "whoops_whats_this": [
                {"id": 1},
            ]
        }
    }
    mocker.patch("fglatch.registry._registry.execute", return_value=bad_response)

    with pytest.raises(ValidationError):
        query_latch_records_by_name(["name_1", "name_2"], table_id="FAKE_TABLE")
