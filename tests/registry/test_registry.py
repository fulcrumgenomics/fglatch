from collections.abc import Iterator
from datetime import datetime
from itertools import islice
from typing import Any

import pytest
from dateutil.parser import isoparse
from graphql import print_ast
from latch.registry.record import NoSuchColumnError
from latch.registry.record import Record
from latch.registry.record import _Cache
from latch.registry.table import Table
from latch.types.directory import LatchDir
from latch.types.file import LatchFile
from latch.types.utils import format_path
from pydantic import ValidationError
from pytest_mock import MockerFixture

from fglatch.registry import LatchRecordModel
from fglatch.registry import fetch_table_records
from fglatch.registry import query_latch_records_by_name
from fglatch.registry._registry import _RECORDS_BY_ID_QUERY
from fglatch.registry._registry import _RECORDS_QUERY
from fglatch.registry._registry import LatchNode
from fglatch.registry._registry import _format_node_path
from fglatch.registry._registry import _preload_file_paths
from fglatch.registry._registry import _preload_linked_record_names
from fglatch.registry._registry import _resolve_node_paths
from fglatch.registry._registry import _rewrite_node_path
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
    """A minimal records response: id, name, and table (experiment) id for each node."""
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


def test_query_latch_records_by_name_offline_preloads_values(
    mocker: MockerFixture,
    fake_values_response: dict[str, Any],
) -> None:
    """Records come back with their values and columns preloaded from the single query, offline."""
    mocker.patch("fglatch.registry._registry.execute", return_value=fake_values_response)

    records = query_latch_records_by_name(["name_1", "name_2"], table_id="999")

    assert records["name_1"].get_values(load_if_missing=False) == {"foo": "hello"}
    assert records["name_2"].get_values(load_if_missing=False) == {"foo": "world"}
    assert records["name_1"].get_columns(load_if_missing=False) is not None


def test_query_latch_records_by_name_collects_conversion_errors(
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
        query_latch_records_by_name(["name_1", "name_2"], table_id="999")

    assert "name_1" in str(excinfo.value)


def test_records_query_excludes_soft_deleted_records() -> None:
    """The by-name query carries the removed-filter clause (a structural guard, not end-to-end)."""
    normalized = "".join(print_ast(_RECORDS_QUERY).split())
    assert "removed:{equalTo:false}" in normalized


def test_by_id_query_does_not_filter_removed() -> None:
    """By-id is keyed on unique ids, so a soft-deleted linked target is still name-primed."""
    normalized = "".join(print_ast(_RECORDS_BY_ID_QUERY).split())
    assert "removed" not in normalized


def _link_column_def(key: str, *, array: bool = False) -> dict[str, Any]:
    """A column definition node for a link (or array-of-link) column."""
    link_type: dict[str, Any] = {"primitive": "link", "experimentId": "555"}
    registry_type = {"array": link_type} if array else link_type
    return {"key": key, "type": {"type": registry_type, "allowEmpty": False}, "def": None}


def _link_value(sample_id: str) -> dict[str, Any]:
    """A registry link value pointing at `sample_id`."""
    return {"value": {"sampleId": sample_id}, "valid": True}


def _values_response(
    record_id: int,
    name: str,
    table_id: int,
    column_defs: list[dict[str, Any]],
    data: list[dict[str, Any]],
) -> dict[str, Any]:
    """A single-node values-query response with the given column definitions and data."""
    return {
        "catalogSamples": {
            "nodes": [
                {
                    "id": record_id,
                    "name": name,
                    "experiment": {
                        "id": table_id,
                        "catalogExperimentColumnDefinitionsByExperimentId": {"nodes": column_defs},
                    },
                    "catalogSampleColumnDataBySampleId": {"nodes": data},
                }
            ]
        }
    }


def test_query_latch_records_by_name_preloads_linked_record_names(mocker: MockerFixture) -> None:
    """A linked record's name is preloaded via one id query, so get_name() needs no network."""
    values_response = _values_response(
        1, "name_1", 999, [_link_column_def("seq")], [{"key": "seq", "data": _link_value("123")}]
    )
    id_response = {
        "catalogSamples": {"nodes": [{"id": 123, "name": "seq_a", "experiment": {"id": 555}}]}
    }
    mocker.patch("fglatch.registry._registry.execute", side_effect=[values_response, id_response])

    records = query_latch_records_by_name("name_1", table_id="999")

    values = records["name_1"].get_values(load_if_missing=False)
    assert values is not None
    linked = values["seq"]
    assert isinstance(linked, Record)
    assert linked.id == "123"
    assert linked.get_name(load_if_missing=False) == "seq_a"


def test_query_latch_records_by_name_preloads_linked_names_in_array_columns(
    mocker: MockerFixture,
) -> None:
    """Linked records inside an array-link column are preloaded too."""
    values_response = _values_response(
        1,
        "name_1",
        999,
        [_link_column_def("seqs", array=True)],
        [{"key": "seqs", "data": [_link_value("123"), _link_value("124")]}],
    )
    id_response = {
        "catalogSamples": {
            "nodes": [
                {"id": 123, "name": "seq_a", "experiment": {"id": 555}},
                {"id": 124, "name": "seq_b", "experiment": {"id": 555}},
            ]
        }
    }
    mocker.patch("fglatch.registry._registry.execute", side_effect=[values_response, id_response])

    records = query_latch_records_by_name("name_1", table_id="999")

    values = records["name_1"].get_values(load_if_missing=False)
    assert values is not None
    linked_list = values["seqs"]
    assert isinstance(linked_list, list)
    names = [r.get_name(load_if_missing=False) for r in linked_list if isinstance(r, Record)]
    assert names == ["seq_a", "seq_b"]


def test_query_latch_records_by_name_primes_returned_ids_when_id_query_omits_one(
    mocker: MockerFixture,
) -> None:
    """A returned id is primed; an id the id-query omits falls back to a lazy per-record load."""
    values_response = _values_response(
        1,
        "name_1",
        999,
        [_link_column_def("seqs", array=True)],
        [{"key": "seqs", "data": [_link_value("123"), _link_value("124")]}],
    )
    # The id query returns 123 but omits 124 (e.g. concurrently removed).
    id_response = {
        "catalogSamples": {"nodes": [{"id": 123, "name": "seq_a", "experiment": {"id": 555}}]}
    }
    mocker.patch("fglatch.registry._registry.execute", side_effect=[values_response, id_response])

    records = query_latch_records_by_name("name_1", table_id="999")

    values = records["name_1"].get_values(load_if_missing=False)
    assert values is not None
    linked = values["seqs"]
    assert isinstance(linked, list)
    by_id = {record.id: record for record in linked if isinstance(record, Record)}
    assert by_id["123"].get_name(load_if_missing=False) == "seq_a"  # primed from the id query
    assert by_id["124"].get_name(load_if_missing=False) is None  # unprimed → lazy fallback


def test_query_latch_records_by_name_skips_id_query_when_no_links(
    mocker: MockerFixture,
    fake_values_response: dict[str, Any],
) -> None:
    """With no linked records, no second (id) query is issued."""
    mock_execute = mocker.patch(
        "fglatch.registry._registry.execute", return_value=fake_values_response
    )

    query_latch_records_by_name(["name_1", "name_2"], table_id="999")

    assert mock_execute.call_count == 1


def test_preload_linked_record_names_ignores_records_without_values(mocker: MockerFixture) -> None:
    """A record whose values are not loaded is skipped (no query, no error)."""
    mock_execute = mocker.patch("fglatch.registry._registry.execute")
    light = LatchNode.model_validate({"id": 1, "name": "r", "experiment": {"id": 999}}).to_record()

    _preload_linked_record_names([light])

    mock_execute.assert_not_called()


def test_query_latch_records_by_name_primes_shared_linked_id_across_records(
    mocker: MockerFixture,
) -> None:
    """Two records linking the same id each prime their own instance, not just one."""

    # to_python_literal mints a fresh Record per link cell, so name_1 and name_2 hold distinct
    # instances of the linked record; both must be primed, or one falls back to a lazy load.
    def _linked(record_id: int, name: str) -> dict[str, Any]:
        return {
            "id": record_id,
            "name": name,
            "experiment": {
                "id": 999,
                "catalogExperimentColumnDefinitionsByExperimentId": {
                    "nodes": [_link_column_def("seq")]
                },
            },
            "catalogSampleColumnDataBySampleId": {
                "nodes": [{"key": "seq", "data": _link_value("123")}]
            },
        }

    values_response = {"catalogSamples": {"nodes": [_linked(1, "name_1"), _linked(2, "name_2")]}}
    id_response = {
        "catalogSamples": {"nodes": [{"id": 123, "name": "seq_a", "experiment": {"id": 555}}]}
    }
    mock_execute = mocker.patch(
        "fglatch.registry._registry.execute", side_effect=[values_response, id_response]
    )

    records = query_latch_records_by_name(["name_1", "name_2"], table_id="999")

    links: list[Record] = []
    for name in ("name_1", "name_2"):
        values = records[name].get_values(load_if_missing=False)
        assert values is not None
        link = values["seq"]
        assert isinstance(link, Record)
        assert link.get_name(load_if_missing=False) == "seq_a"  # primed — no lazy load
        links.append(link)

    assert links[0] is not links[1]  # distinct instances of the same id, each primed
    assert mock_execute.call_count == 2  # values query + one id query, no lazy fallback


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


# Each SDK `ldataGetPath` shape, its `ldataOwner`, and the readable path `format_path` produces.
# `mount_gcp`/`mount_azure` resolve to `.mount`: the SDK's unanchored `old_style_path` regex lets
# the `mount` alternative shadow them. These cases pin that (quirky) parity with `format_path`.
PATH_SHAPES: list[tuple[str, str | None, str]] = [
    ("mount/mybucket/a/b.txt", None, "latch://mybucket.mount/a/b.txt"),
    ("mount_gcp/mybucket/a/b.txt", None, "latch://mybucket.mount/a/b.txt"),
    ("mount_azure/mybucket/a/b.txt", None, "latch://mybucket.mount/a/b.txt"),
    ("account_root/123/a/b.txt", "123", "latch://123.account/a/b.txt"),
]


@pytest.mark.parametrize(
    ("raw", "owner", "expected"), PATH_SHAPES, ids=[s[0].split("/")[0] for s in PATH_SHAPES]
)
def test_format_node_path_matches_each_shape(raw: str, owner: str | None, expected: str) -> None:
    """`_format_node_path` reproduces `format_path`'s local formatting for each path shape."""
    assert _format_node_path(raw, owner) == expected


@pytest.mark.parametrize(
    ("raw", "owner", "expected"), PATH_SHAPES, ids=[s[0].split("/")[0] for s in PATH_SHAPES]
)
def test_resolve_node_paths_parity_with_format_path(
    mocker: MockerFixture, raw: str, owner: str | None, expected: str
) -> None:
    """`_resolve_node_paths([id])[id]` equals `format_path(f"latch://{id}.node")` per shape."""
    mocker.patch("fglatch.registry._registry.execute", return_value={"p0": raw, "o0": owner})
    mocker.patch(
        "latch.types.utils.execute", return_value={"ldataGetPath": raw, "ldataOwner": owner}
    )

    assert _resolve_node_paths(["5"])["5"] == format_path("latch://5.node") == expected


def test_resolve_node_paths_deduplicates(mocker: MockerFixture) -> None:
    """Repeated ids collapse to one aliased lookup and one map entry."""
    execute_mock = mocker.patch(
        "fglatch.registry._registry.execute",
        return_value={"p0": "mount/b/k", "o0": None, "p1": "mount/b/k2", "o1": None},
    )

    result = _resolve_node_paths(["1", "1", "2"])

    execute_mock.assert_called_once()
    assert set(execute_mock.call_args.kwargs["variables"]) == {"id0", "id1"}
    assert set(result) == {"1", "2"}


def test_resolve_node_paths_chunks(mocker: MockerFixture) -> None:
    """N ids over chunk_size issue ceil(N / chunk_size) queries, partitioned in order."""
    execute_mock = mocker.patch(
        "fglatch.registry._registry.execute",
        side_effect=[
            {"p0": "mount/b/k1", "o0": None, "p1": "mount/b/k2", "o1": None},
            {"p0": "mount/b/k3", "o0": None},
        ],
    )

    result = _resolve_node_paths(["1", "2", "3"], chunk_size=2)

    assert execute_mock.call_count == 2
    assert execute_mock.call_args_list[0].kwargs["variables"] == {"id0": "1", "id1": "2"}
    assert execute_mock.call_args_list[1].kwargs["variables"] == {"id0": "3"}
    assert result == {
        "1": "latch://b.mount/k1",
        "2": "latch://b.mount/k2",
        "3": "latch://b.mount/k3",
    }


def test_resolve_node_paths_omits_unresolvable(mocker: MockerFixture) -> None:
    """A node whose path cannot be resolved is omitted from the result."""
    mocker.patch(
        "fglatch.registry._registry.execute",
        return_value={"p0": None, "o0": None, "p1": "mount/b/k", "o1": None},
    )

    result = _resolve_node_paths(["missing", "ok"])

    assert "missing" not in result
    assert result["ok"] == "latch://b.mount/k"


def test_resolve_node_paths_empty_input_makes_no_query(mocker: MockerFixture) -> None:
    """An empty id list returns `{}` without issuing a query."""
    execute_mock = mocker.patch("fglatch.registry._registry.execute")

    assert _resolve_node_paths([]) == {}
    execute_mock.assert_not_called()


@pytest.mark.parametrize(
    ("raw", "owner"),
    [
        pytest.param(None, None, id="none-path"),
        pytest.param("not_an_old_style_path", None, id="unrecognized-prefix"),
        pytest.param("account_root/123/a/b.txt", None, id="account-root-without-owner"),
    ],
)
def test_format_node_path_returns_none_for_unresolvable(raw: str | None, owner: str | None) -> None:
    """`_format_node_path` returns None (caller omits/falls back) when it cannot format."""
    assert _format_node_path(raw, owner) is None


def test_resolve_node_paths_collects_and_raises_all_chunk_errors(mocker: MockerFixture) -> None:
    """A chunk failure does not stop the others; every failure is surfaced in one raised error."""
    mocker.patch(
        "fglatch.registry._registry.execute",
        side_effect=[
            {"p0": "mount/b/k", "o0": None},
            RuntimeError("boom-2"),
            RuntimeError("boom-3"),
        ],
    )

    with pytest.raises(RuntimeError, match="2 chunk") as excinfo:
        _resolve_node_paths(["1", "2", "3"], chunk_size=1)

    message = str(excinfo.value)
    assert "boom-2" in message
    assert "boom-3" in message


@pytest.mark.parametrize(
    ("value", "node_paths", "expected"),
    [
        pytest.param(
            LatchFile("latch://1.node"),
            {"1": "latch://b.mount/a.txt"},
            ("LatchFile", "latch://b.mount/a.txt"),
            id="file-hit-rewritten",
        ),
        pytest.param(
            LatchDir("latch://2.node"),
            {"2": "latch://b.mount/d"},
            ("LatchDir", "latch://b.mount/d"),
            id="dir-hit-rewritten",
        ),
        pytest.param(
            LatchFile("latch://1.node"),
            {"9": "latch://b.mount/x"},
            ("LatchFile", "latch://1.node"),
            id="miss-keeps-raw",
        ),
        pytest.param(
            LatchFile("latch://b.mount/a.txt"),
            {"1": "latch://b.mount/other"},
            ("LatchFile", "latch://b.mount/a.txt"),
            id="already-formatted-untouched",
        ),
        pytest.param("plain-string", {"1": "latch://b.mount/x"}, "plain-string", id="non-file"),
    ],
)
def test_rewrite_node_path(value: Any, node_paths: dict[str, str], expected: Any) -> None:
    """A node-path file cell is rebuilt on a hit; miss / already-readable / non-file are kept."""
    result = _rewrite_node_path(value, node_paths)

    if isinstance(result, (LatchFile, LatchDir)):
        assert (type(result).__name__, result.remote_path) == expected
    else:
        assert result == expected


def cache_with_values(*, name: str, values: dict[str, Any]) -> _Cache:
    """A `_Cache` shaped like `Table.list_records` output, with the given values (for tests)."""
    now = datetime(2024, 1, 1)
    return _Cache(
        table_id="1", name=name, creation_time=now, last_updated=now, columns={}, values=values
    )


def test_preload_file_paths_rewrites_scalar_and_list_cells(mocker: MockerFixture) -> None:
    """File/dir cells (scalar and in a list) are rewritten in place from the resolved paths."""
    record = Record("1")
    object.__setattr__(
        record,
        "_cache",
        cache_with_values(
            name="r", values={"f": LatchFile("latch://1.node"), "fs": [LatchDir("latch://2.node")]}
        ),
    )
    mocker.patch(
        "fglatch.registry._registry.execute",
        return_value={"p0": "mount/b/a.txt", "o0": None, "p1": "mount/b/d", "o1": None},
    )

    _preload_file_paths([record])

    values = record.get_values(load_if_missing=False)
    assert values is not None
    scalar, listed = values["f"], values["fs"]
    assert isinstance(scalar, LatchFile)
    assert isinstance(listed, list) and isinstance(listed[0], LatchDir)
    assert scalar.remote_path == "latch://b.mount/a.txt"
    assert listed[0].remote_path == "latch://b.mount/d"


def test_preload_file_paths_no_files_makes_no_query(mocker: MockerFixture) -> None:
    """With no file cells, no node-path query is issued."""
    record = Record("1")
    object.__setattr__(record, "_cache", cache_with_values(name="r", values={"n": 3}))
    execute_mock = mocker.patch("fglatch.registry._registry.execute")

    _preload_file_paths([record])

    execute_mock.assert_not_called()


def test_resolve_node_paths_rejects_nonpositive_chunk_size() -> None:
    """A chunk_size below 1 is rejected before any query is issued."""
    with pytest.raises(ValueError, match="chunk_size must be >= 1"):
        _resolve_node_paths(["1"], chunk_size=0)


def test_preload_file_paths_skips_records_without_values(mocker: MockerFixture) -> None:
    """A record whose values are not loaded is skipped while other records' cells are rewritten."""
    with_file = Record("1")
    object.__setattr__(
        with_file, "_cache", cache_with_values(name="r", values={"f": LatchFile("latch://1.node")})
    )
    without_values = LatchNode.model_validate({
        "id": 2,
        "name": "r2",
        "experiment": {"id": 999},
    }).to_record()
    mocker.patch(
        "fglatch.registry._registry.execute",
        return_value={"p0": "mount/b/a.txt", "o0": None},
    )

    _preload_file_paths([with_file, without_values])

    values = with_file.get_values(load_if_missing=False)
    assert values is not None
    assert isinstance(values["f"], LatchFile)
    assert values["f"].remote_path == "latch://b.mount/a.txt"
    assert without_values.get_values(load_if_missing=False) is None


def _named_record(record_id: str, name: str) -> Record:
    """A `Record` with `name` primed and empty values (so preload issues no query)."""
    record = Record(record_id)
    object.__setattr__(record, "_cache", cache_with_values(name=name, values={}))
    return record


def test_fetch_table_records_preloads_links_and_files(mocker: MockerFixture) -> None:
    """Every yielded record has its linked names and file/dir paths preloaded."""
    record = Record("1")
    object.__setattr__(
        record,
        "_cache",
        cache_with_values(
            name="r", values={"seq": Record("123"), "f": LatchFile("latch://9.node")}
        ),
    )
    mocker.patch(
        "fglatch.registry._registry.Table.list_records", return_value=iter([{"1": record}])
    )
    # Both preloads route through the same execute: linked-name query, then node-path query.
    mocker.patch(
        "fglatch.registry._registry.execute",
        side_effect=[
            {"catalogSamples": {"nodes": [{"id": 123, "name": "seq_a", "experiment": {"id": 5}}]}},
            {"p0": "mount/b/a.txt", "o0": None},
        ],
    )

    records = list(fetch_table_records("999"))

    assert len(records) == 1
    values = records[0].get_values(load_if_missing=False)
    assert values is not None
    seq, file_cell = values["seq"], values["f"]
    assert isinstance(seq, Record)
    assert isinstance(file_cell, LatchFile)
    assert seq.get_name(load_if_missing=False) == "seq_a"
    assert file_cell.remote_path == "latch://b.mount/a.txt"


def test_fetch_table_records_yields_from_every_page(mocker: MockerFixture) -> None:
    """Records from every page are yielded, in order."""
    mocker.patch(
        "fglatch.registry._registry.Table.list_records",
        return_value=iter([{"1": _named_record("1", "r1")}, {"2": _named_record("2", "r2")}]),
    )

    names = [record.get_name(load_if_missing=False) for record in fetch_table_records("999")]

    assert names == ["r1", "r2"]


def test_fetch_table_records_empty_table_yields_nothing(mocker: MockerFixture) -> None:
    """A table with no records yields nothing."""
    mocker.patch("fglatch.registry._registry.Table.list_records", return_value=iter([]))

    assert list(fetch_table_records("999")) == []


def test_fetch_table_records_streams_lazily(mocker: MockerFixture) -> None:
    """Records are yielded lazily, so taking a prefix does not force every page to be built."""
    pages_pulled = 0

    def pages() -> Iterator[dict[str, Record]]:
        nonlocal pages_pulled
        for name in ("r1", "r2", "r3"):
            pages_pulled += 1
            yield {name: _named_record(name, name)}

    mocker.patch("fglatch.registry._registry.Table.list_records", return_value=pages())

    prefix = list(islice(fetch_table_records("999"), 2))

    assert [record.get_name(load_if_missing=False) for record in prefix] == ["r1", "r2"]
    assert pages_pulled == 2  # the third page was never pulled


def test_fetch_table_records_forwards_page_size(mocker: MockerFixture) -> None:
    """page_size is threaded through to Table.list_records."""
    list_records = mocker.patch(
        "fglatch.registry._registry.Table.list_records", return_value=iter([])
    )

    list(fetch_table_records("999", page_size=25))

    list_records.assert_called_once_with(page_size=25)


def test_fetch_table_records_rejects_nonpositive_page_size() -> None:
    """A page_size below 1 is rejected eagerly, before any records are fetched."""
    with pytest.raises(ValueError, match="page_size must be >= 1"):
        fetch_table_records("999", page_size=0)
