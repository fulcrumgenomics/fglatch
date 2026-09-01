from typing import Any

import pytest
from dateutil.parser import isoparse
from latch.registry.record import NoSuchColumnError
from latch.registry.record import Record
from latch.registry.table import Table
from pydantic import ValidationError
from pytest_mock import MockerFixture

from fglatch.registry import LatchRecordModel
from fglatch.registry import query_latch_records_by_name
from fglatch.registry._registry import LatchNode
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
    """A fake light-query response: id, name, and table (experiment) id for each node."""
    return {
        "catalogSamples": {
            "nodes": [
                {"id": 1, "name": "name_1", "experiment": {"id": 999}},
                {"id": 2, "name": "name_2", "experiment": {"id": 999}},
            ]
        }
    }


def test_query_latch_records_by_name_offline(
    mocker: MockerFixture,
    fake_gql_response: dict[str, Any],
) -> None:
    """It returns real records keyed by name, with name and table id preloaded from the query."""
    mocker.patch("fglatch.registry._registry.execute", return_value=fake_gql_response)

    records: dict[RecordName, Record] = query_latch_records_by_name(
        ["name_1", "name_2"],
        table_id="999",
    )

    assert set(records) == {"name_1", "name_2"}
    assert records["name_1"].id == "1"

    # Name and table id are preloaded from the query, so no network load is needed to read them.
    assert records["name_1"].get_name(load_if_missing=False) == "name_1"
    assert records["name_1"].get_table_id(load_if_missing=False) == "999"


def test_query_latch_records_by_name_offline_filters_to_requested_table(
    mocker: MockerFixture,
) -> None:
    """Records that share a name across tables are filtered down to the requested table."""
    response = {
        "catalogSamples": {
            "nodes": [
                {"id": 1, "name": "dup", "experiment": {"id": 111}},
                {"id": 2, "name": "dup", "experiment": {"id": 222}},
            ]
        }
    }
    mocker.patch("fglatch.registry._registry.execute", return_value=response)

    records: dict[RecordName, Record] = query_latch_records_by_name("dup", table_id="111")

    assert set(records) == {"dup"}
    assert records["dup"].id == "1"
    assert records["dup"].get_table_id(load_if_missing=False) == "111"


def test_query_latch_records_by_name_raises_if_no_record_returned_by_gql(
    mocker: MockerFixture,
) -> None:
    """Should raise a ValueError if a Record isn't returned for one of the requested names."""
    response = {
        "catalogSamples": {
            "nodes": [
                {"id": 1, "name": "name_1", "experiment": {"id": 999}},
            ]
        }
    }
    mocker.patch("fglatch.registry._registry.execute", return_value=response)

    with pytest.raises(ValueError) as excinfo:
        query_latch_records_by_name(["name_1", "name_2"], table_id="999")

    assert "No record found with name: name_2" in str(excinfo.value)


def test_query_latch_records_by_name_raises_if_duplicate_records_returned_by_gql(
    mocker: MockerFixture,
) -> None:
    """Should raise a ValueError if multiple records in the table share the same name."""
    response = {
        "catalogSamples": {
            "nodes": [
                {"id": 1, "name": "name_1", "experiment": {"id": 999}},
                {"id": 2, "name": "name_2", "experiment": {"id": 999}},
                {"id": 3, "name": "name_1", "experiment": {"id": 999}},  # collides with node 1
            ]
        }
    }
    mocker.patch("fglatch.registry._registry.execute", return_value=response)

    with pytest.raises(ValueError) as excinfo:
        query_latch_records_by_name(["name_1", "name_2"], table_id="999")

    assert "Duplicate record name: name_1 (n=2)" in str(excinfo.value)


def test_query_latch_records_by_name_raises_if_response_cannot_be_validated(
    mocker: MockerFixture,
) -> None:
    """Should raise a ValidationError if the GQL response can't be validated."""
    bad_response = {
        "catalogSamples": {
            "whoops_whats_this": [
                {"id": 1},
            ]
        }
    }
    mocker.patch("fglatch.registry._registry.execute", return_value=bad_response)

    with pytest.raises(ValidationError):
        query_latch_records_by_name(["name_1", "name_2"], table_id="999")


@pytest.fixture
def fake_values_response() -> dict[str, Any]:
    """A fake values-query response: id, name, table id, column definitions, and data per node."""
    return {
        "catalogSamples": {
            "nodes": [
                {
                    "id": 1,
                    "name": "name_1",
                    "experiment": {
                        "id": 999,
                        "catalogExperimentColumnDefinitionsByExperimentId": {
                            "nodes": [
                                {
                                    "key": "foo",
                                    "type": {"type": {"primitive": "string"}, "allowEmpty": False},
                                    "def": None,
                                }
                            ]
                        },
                    },
                    "catalogSampleColumnDataBySampleId": {
                        "nodes": [{"key": "foo", "data": {"value": "hello", "valid": True}}]
                    },
                },
                {
                    "id": 2,
                    "name": "name_2",
                    "experiment": {
                        "id": 999,
                        "catalogExperimentColumnDefinitionsByExperimentId": {
                            "nodes": [
                                {
                                    "key": "foo",
                                    "type": {"type": {"primitive": "string"}, "allowEmpty": False},
                                    "def": None,
                                }
                            ]
                        },
                    },
                    "catalogSampleColumnDataBySampleId": {
                        "nodes": [{"key": "foo", "data": {"value": "world", "valid": True}}]
                    },
                },
            ]
        }
    }


def test_query_latch_records_by_name_offline_load_values_preloads_values(
    mocker: MockerFixture,
    fake_values_response: dict[str, Any],
) -> None:
    """With load_values=True, records come back with their values and columns preloaded offline."""
    mocker.patch("fglatch.registry._registry.execute", return_value=fake_values_response)

    records = query_latch_records_by_name(["name_1", "name_2"], table_id="999", load_values=True)

    assert records["name_1"].get_values(load_if_missing=False) == {"foo": "hello"}
    assert records["name_2"].get_values(load_if_missing=False) == {"foo": "world"}
    assert records["name_1"].get_columns(load_if_missing=False) is not None


def test_query_latch_records_by_name_offline_defaults_to_not_preloading_values(
    mocker: MockerFixture,
    fake_gql_response: dict[str, Any],
) -> None:
    """By default values are not fetched, so they remain lazily loaded on first access."""
    mocker.patch("fglatch.registry._registry.execute", return_value=fake_gql_response)

    records = query_latch_records_by_name(["name_1", "name_2"], table_id="999")

    assert records["name_1"].get_values(load_if_missing=False) is None


def test_query_latch_records_by_name_load_values_collects_conversion_errors(
    mocker: MockerFixture,
) -> None:
    """A per-record value conversion failure is collected and raised once, naming the record."""
    response = {
        "catalogSamples": {
            "nodes": [
                {
                    "id": 1,
                    "name": "name_1",
                    "experiment": {
                        "id": 999,
                        "catalogExperimentColumnDefinitionsByExperimentId": {
                            "nodes": [
                                {
                                    "key": "n",
                                    "type": {"type": {"primitive": "integer"}, "allowEmpty": False},
                                    "def": None,
                                }
                            ]
                        },
                    },
                    "catalogSampleColumnDataBySampleId": {
                        "nodes": [{"key": "n", "data": {"value": "not-an-int", "valid": True}}]
                    },
                },
                {
                    "id": 2,
                    "name": "name_2",
                    "experiment": {
                        "id": 999,
                        "catalogExperimentColumnDefinitionsByExperimentId": {
                            "nodes": [
                                {
                                    "key": "n",
                                    "type": {"type": {"primitive": "integer"}, "allowEmpty": False},
                                    "def": None,
                                }
                            ]
                        },
                    },
                    "catalogSampleColumnDataBySampleId": {
                        "nodes": [{"key": "n", "data": {"value": 5, "valid": True}}]
                    },
                },
            ]
        }
    }
    mocker.patch("fglatch.registry._registry.execute", return_value=response)

    with pytest.raises(ValueError, match="Could not query records by name") as excinfo:
        query_latch_records_by_name(["name_1", "name_2"], table_id="999", load_values=True)

    assert "name_1" in str(excinfo.value)


class MockRecord(LatchRecordModel):
    """
    A fake record for testing.

    Corresponds to `mock-table-1` (id=11730) in the Fulcrum workspace.
    """

    foo: str
    bar: int


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


@pytest.fixture
def full_catalog_sample() -> dict[str, Any]:
    """A `catalogSample` node from the values query: id, name, table id, column defs, and data."""
    return {
        "id": 1,
        "name": "mock_record_1",
        "experiment": {
            "id": 999,
            "catalogExperimentColumnDefinitionsByExperimentId": {
                "nodes": [
                    {
                        "key": "foo",
                        "type": {"type": {"primitive": "string"}, "allowEmpty": False},
                        "def": None,
                    },
                    {
                        "key": "bar",
                        "type": {"type": {"primitive": "integer"}, "allowEmpty": False},
                        "def": None,
                    },
                    {
                        "key": "baz",
                        "type": {"type": {"primitive": "string"}, "allowEmpty": True},
                        "def": None,
                    },
                ]
            },
        },
        "catalogSampleColumnDataBySampleId": {
            "nodes": [
                {"key": "foo", "data": {"value": "hello", "valid": True}},
                {"key": "bar", "data": {"value": 42, "valid": True}},
            ]
        },
    }


def test_to_cache_preloads_name_table_and_values(
    full_catalog_sample: dict[str, Any],
) -> None:
    """It builds a `_Cache` with the sample's name, table id, columns, and converted values."""
    node = LatchNode.model_validate(full_catalog_sample)

    cache = node.to_cache()

    assert cache.name == "mock_record_1"
    assert cache.table_id == "999"

    assert cache.columns is not None
    assert set(cache.columns) == {"foo", "bar", "baz"}
    assert cache.columns["foo"].type is str
    assert cache.columns["bar"].type is int

    assert cache.values is not None
    assert cache.values["foo"] == "hello"
    assert cache.values["bar"] == 42


def test_to_cache_maps_missing_value_to_none(
    full_catalog_sample: dict[str, Any],
) -> None:
    """
    A column with no datum resolves to `None`, matching `Record.load()`.

    `Record.load()` writes `InvalidValue("")` for a missing required value and then unconditionally
    overwrites it with `None` (record.py:200-204), so every missing value ends up `None` regardless
    of whether the column is required. We mirror that quirk so preloaded records are
    indistinguishable from lazily-loaded ones.
    """
    # Add a required (allowEmpty=False) column that has no datum, alongside the optional "baz".
    full_catalog_sample["experiment"]["catalogExperimentColumnDefinitionsByExperimentId"][
        "nodes"
    ].append({
        "key": "qux",
        "type": {"type": {"primitive": "string"}, "allowEmpty": False},
        "def": None,
    })
    node = LatchNode.model_validate(full_catalog_sample)

    cache = node.to_cache()

    assert cache.values is not None
    assert cache.values["baz"] is None  # optional column, missing datum
    assert cache.values["qux"] is None  # required column, missing datum (InvalidValue overwritten)


def test_to_cache_raises_on_partial_values_response(
    full_catalog_sample: dict[str, Any],
) -> None:
    """A node with column definitions but no column data (a malformed response) raises."""
    del full_catalog_sample["catalogSampleColumnDataBySampleId"]
    node = LatchNode.model_validate(full_catalog_sample)

    with pytest.raises(RuntimeError, match="column definitions or data"):
        node.to_cache()


def test_to_cache_raises_on_datum_without_definition(
    full_catalog_sample: dict[str, Any],
) -> None:
    """A value whose column key has no definition raises `NoSuchColumnError`, as `Record.load()`."""
    full_catalog_sample["catalogSampleColumnDataBySampleId"]["nodes"].append({
        "key": "ghost",
        "data": {"value": "boo", "valid": True},
    })
    node = LatchNode.model_validate(full_catalog_sample)

    with pytest.raises(NoSuchColumnError):
        node.to_cache()


def _values_node_with_timestamps(creation_time: str, event_times: list[str]) -> dict[str, Any]:
    """A minimal values-query node (no columns) carrying a creation time and event times."""
    return {
        "id": 1,
        "name": "r",
        "creationTime": creation_time,
        "catalogEventsBySampleId": {"nodes": [{"time": t} for t in event_times]},
        "experiment": {
            "id": 999,
            "catalogExperimentColumnDefinitionsByExperimentId": {"nodes": []},
        },
        "catalogSampleColumnDataBySampleId": {"nodes": []},
    }


def test_to_cache_preloads_timestamps() -> None:
    """It preloads creation_time and last_updated from the latest event, per `Record.load()`."""
    node = LatchNode.model_validate(
        _values_node_with_timestamps("2024-01-01T00:00:00+00:00", ["2024-06-01T12:00:00+00:00"])
    )

    cache = node.to_cache()

    assert cache.creation_time == isoparse("2024-01-01T00:00:00+00:00")
    assert cache.last_updated == isoparse("2024-06-01T12:00:00+00:00")


def test_to_cache_last_updated_falls_back_to_creation_time() -> None:
    """With no events, last_updated falls back to creation_time, matching `Record.load()`."""
    node = LatchNode.model_validate(_values_node_with_timestamps("2024-01-01T00:00:00+00:00", []))

    cache = node.to_cache()

    assert cache.creation_time == isoparse("2024-01-01T00:00:00+00:00")
    assert cache.last_updated == cache.creation_time


def test_to_cache_light_node_omits_columns_and_values() -> None:
    """A light node (no column data) yields a cache with name and table id but no values."""
    node = LatchNode.model_validate({"id": 1, "name": "name_1", "experiment": {"id": 999}})

    cache = node.to_cache()

    assert cache.name == "name_1"
    assert cache.table_id == "999"
    assert cache.columns is None
    assert cache.values is None


def test_to_record_preloads_values(full_catalog_sample: dict[str, Any]) -> None:
    """A values node's record has its columns and values readable without a network load."""
    node = LatchNode.model_validate(full_catalog_sample)

    record = node.to_record()

    assert record.id == "1"
    assert record.get_name(load_if_missing=False) == "mock_record_1"
    assert record.get_values(load_if_missing=False) == {"foo": "hello", "bar": 42, "baz": None}


def test_to_record_light_node_leaves_values_lazy() -> None:
    """A light node's record preloads name and table id but leaves values lazy."""
    node = LatchNode.model_validate({"id": 1, "name": "name_1", "experiment": {"id": 999}})

    record = node.to_record()

    assert record.get_name(load_if_missing=False) == "name_1"
    assert record.get_table_id(load_if_missing=False) == "999"
    assert record.get_values(load_if_missing=False) is None
