import logging
from typing import Any

import pytest
from graphql import print_ast
from latch.registry.record import Record
from latch.registry.table import Table
from latch.registry.table import TableNotFoundError
from latch.registry.types import InvalidValue
from pydantic import ValidationError
from pytest_mock import MockerFixture

from fglatch.registry import LatchRecordModel
from fglatch.registry import query_latch_records_by_name
from fglatch.registry._registry import _QUERY
from fglatch.type_aliases import RecordName
from tests.constants import MOCK_TABLE_1_ID

# ────────────────────────────────── Online tests ──────────────────────────────────


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
def test_query_latch_records_by_name_online_default_primes_values(mocker: MockerFixture) -> None:
    """By default, name/table_id/values are all primed (no per-record load) and match load()."""
    load_spy = mocker.spy(Record, "load")

    records = query_latch_records_by_name("mock_record_1", table_id=MOCK_TABLE_1_ID)
    record = records["mock_record_1"]

    assert record.get_name() == "mock_record_1"
    assert record.get_table_id() == MOCK_TABLE_1_ID
    primed_values = record.get_values()
    assert load_spy.call_count == 0  # everything primed — no round trip

    fresh = Record(record.id)
    fresh.load()
    assert primed_values == fresh.get_values()  # eager values equal a freshly loaded record's


@pytest.mark.requires_latch_registry
def test_query_latch_records_by_name_online_defer_values_loads_lazily(
    mocker: MockerFixture,
) -> None:
    """With defer_values, name/table_id are primed but values load lazily on first access."""
    load_spy = mocker.spy(Record, "load")

    records = query_latch_records_by_name(
        "mock_record_1", table_id=MOCK_TABLE_1_ID, defer_values=True
    )
    record = records["mock_record_1"]

    assert record.get_name() == "mock_record_1"
    assert record.get_table_id() == MOCK_TABLE_1_ID
    assert load_spy.call_count == 0  # name and table_id are primed

    assert record.get_values().get("foo") == "hello"
    assert load_spy.call_count == 1  # values were not primed — loaded lazily


@pytest.mark.requires_latch_registry
def test_query_latch_records_by_name_online_eager_and_deferred_values_agree() -> None:
    """Eager-primed values, the deferred path's lazy load, and Record.load() all agree."""
    name: str = "mock_record_1"
    eager = query_latch_records_by_name(name, table_id=MOCK_TABLE_1_ID)[name]
    deferred = query_latch_records_by_name(name, table_id=MOCK_TABLE_1_ID, defer_values=True)[name]

    fresh = Record(eager.id)
    fresh.load()

    assert eager.get_values(load_if_missing=False) == fresh.get_values()  # eager == load()
    assert deferred.get_values() == fresh.get_values()  # deferred lazy-load == load()


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


# ────────────────────────────────── Offline tests ──────────────────────────────────
# `foo` is required (allowEmpty=False); `bar` and `baz` are optional (allowEmpty=True).
FAKE_TABLE_ID: str = "FAKE_TABLE"
FAKE_COLUMN_DEFS: list[dict[str, Any]] = [
    {"key": "foo", "type": {"type": {"primitive": "string"}, "allowEmpty": False}},
    {"key": "bar", "type": {"type": {"primitive": "integer"}, "allowEmpty": True}},
    {"key": "baz", "type": {"type": {"primitive": "string"}, "allowEmpty": True}},
]


def _valid(value: Any) -> dict[str, Any]:
    """A valid stored Registry value."""
    return {"value": value, "valid": True}


def _node(record_id: int, name: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """A fake `catalogSamplesByExperimentId` node (ids are strings, as the API returns them)."""
    node: dict[str, Any] = {"id": str(record_id), "name": name}
    if data is not None:
        node["catalogSampleColumnDataBySampleId"] = {
            "nodes": [{"key": key, "data": value} for key, value in data.items()]
        }
    return node


def _response(*nodes: dict[str, Any], with_values: bool = True) -> dict[str, Any]:
    """A fake `execute()` response. When `with_values`, includes column defs and per-node data."""
    node_list = list(nodes)
    experiment: dict[str, Any] = {
        "id": FAKE_TABLE_ID,
        "catalogSamplesByExperimentId": {"nodes": node_list},
    }
    if with_values:
        experiment["catalogExperimentColumnDefinitionsByExperimentId"] = {"nodes": FAKE_COLUMN_DEFS}
        for node in node_list:
            node.setdefault("catalogSampleColumnDataBySampleId", {"nodes": []})
    return {"catalogExperiment": experiment}


def _load_response(data: dict[str, Any]) -> dict[str, Any]:
    """A fake `Record.load()` response (its `catalogSample` query shape) with the given values."""
    return {
        "catalogSample": {
            "id": "1",
            "name": "r",
            "creationTime": "2024-01-01T00:00:00+00:00",
            "catalogEventsBySampleId": {"nodes": []},
            "catalogSampleColumnDataBySampleId": {
                "nodes": [{"key": key, "data": value} for key, value in data.items()]
            },
            "experiment": {
                "id": FAKE_TABLE_ID,
                "catalogExperimentColumnDefinitionsByExperimentId": {"nodes": FAKE_COLUMN_DEFS},
            },
        }
    }


def test_query_latch_records_by_name_offline_eager_primes_metadata_and_values(
    mocker: MockerFixture,
) -> None:
    """By default, each record is keyed by name with name, table_id, and values all primed."""
    response = _response(
        _node(1, "name_1", {"foo": _valid("hello")}),
        _node(2, "name_2", {"foo": _valid("world")}),
    )
    mocker.patch("fglatch.registry._registry.execute", return_value=response)

    records = query_latch_records_by_name(["name_1", "name_2"], table_id=FAKE_TABLE_ID)

    assert set(records) == {"name_1", "name_2"}
    record_1 = records["name_1"]
    assert record_1.get_name(load_if_missing=False) == "name_1"
    assert record_1.get_table_id(load_if_missing=False) == FAKE_TABLE_ID
    assert record_1.get_values(load_if_missing=False) == {"foo": "hello", "bar": None, "baz": None}


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        pytest.param(
            {"foo": _valid("hello"), "bar": _valid(42)},
            {"foo": "hello", "bar": 42, "baz": None},
            id="present-values-and-missing-optional-is-None",
        ),
        pytest.param(
            {"foo": _valid("hi")},
            {"foo": "hi", "bar": None, "baz": None},
            id="all-missing-optional-are-None",
        ),
        pytest.param(
            {"bar": _valid(7)},
            {"foo": None, "bar": 7, "baz": None},
            id="missing-required-foo-is-None-matching-Record-load",
        ),
        pytest.param(
            {"foo": {"valid": False, "rawValue": "oops"}},
            {"foo": InvalidValue("oops"), "bar": None, "baz": None},
            id="invalid-stored-value-becomes-InvalidValue",
        ),
        pytest.param(
            {"foo": _valid("hi"), "ghost": _valid("x")},
            {"foo": "hi", "bar": None, "baz": None},
            id="unknown-column-key-is-skipped",
        ),
    ],
)
def test_query_latch_records_by_name_offline_parses_values(
    mocker: MockerFixture, stored: dict[str, Any], expected: dict[str, Any]
) -> None:
    """Eager values fill every column, `None` for missing, matching `Record.load()`."""
    mocker.patch(
        "fglatch.registry._registry.execute", return_value=_response(_node(1, "r", stored))
    )

    records = query_latch_records_by_name("r", table_id=FAKE_TABLE_ID)

    assert records["r"].get_values(load_if_missing=False) == expected


def test_query_latch_records_by_name_offline_parses_array_column(mocker: MockerFixture) -> None:
    """An array-typed column's value arrives as a bare JSON list (not a dict) and must parse."""
    # `Oligo Lot`/`Expressed Protein` have real array-of-link columns; here an array of links.
    column_defs = [
        {"key": "genes", "type": {"type": {"array": {"primitive": "link", "experimentId": "9"}}}}
    ]
    node = {
        "id": "1",
        "name": "r",
        "catalogSampleColumnDataBySampleId": {
            "nodes": [{"key": "genes", "data": [{"value": {"sampleId": "42"}, "valid": True}]}]
        },
    }
    response = {
        "catalogExperiment": {
            "id": FAKE_TABLE_ID,
            "catalogExperimentColumnDefinitionsByExperimentId": {"nodes": column_defs},
            "catalogSamplesByExperimentId": {"nodes": [node]},
        }
    }
    mocker.patch("fglatch.registry._registry.execute", return_value=response)

    records = query_latch_records_by_name("r", table_id=FAKE_TABLE_ID)

    assert records["r"].get_values(load_if_missing=False) == {"genes": [Record("42")]}


def test_query_latch_records_by_name_offline_ignores_duplicate_input_names(
    mocker: MockerFixture,
) -> None:
    """A name repeated in the input collapses to a single result entry."""
    mocker.patch("fglatch.registry._registry.execute", return_value=_response(_node(1, "r")))

    records = query_latch_records_by_name(["r", "r"], table_id=FAKE_TABLE_ID)

    assert set(records) == {"r"}


def test_query_latch_records_by_name_offline_defer_values_primes_metadata_only(
    mocker: MockerFixture,
) -> None:
    """With defer_values, name and table_id are primed but values stay lazy (not primed)."""
    response = _response(_node(1, "r"), with_values=False)
    mocker.patch("fglatch.registry._registry.execute", return_value=response)

    records = query_latch_records_by_name("r", table_id=FAKE_TABLE_ID, defer_values=True)

    record = records["r"]
    assert record.get_name(load_if_missing=False) == "r"
    assert record.get_table_id(load_if_missing=False) == FAKE_TABLE_ID
    assert record.get_values(load_if_missing=False) is None


@pytest.mark.parametrize(
    ("defer_values", "with_values"),
    [(False, True), (True, False)],
    ids=["eager", "defer"],
)
def test_query_latch_records_by_name_offline_scopes_query(
    mocker: MockerFixture, defer_values: bool, with_values: bool
) -> None:
    """The fetch is a single round trip scoped to the table, names, and value selection."""
    execute_mock = mocker.patch(
        "fglatch.registry._registry.execute",
        return_value=_response(_node(1, "r"), with_values=with_values),
    )

    query_latch_records_by_name("r", table_id=FAKE_TABLE_ID, defer_values=defer_values)

    execute_mock.assert_called_once()
    assert execute_mock.call_args.kwargs["variables"] == {
        "tableId": FAKE_TABLE_ID,
        "sampleNames": ["r"],
        "withValues": with_values,
    }


def test_query_latch_records_by_name_query_filters_out_removed_records() -> None:
    """
    The query must exclude soft-deleted records so a live-unique name is not a false duplicate.

    Latch's name-uniqueness constraint holds only over live records, but
    `catalogSamplesByExperimentId` returns removed records too. A name that is unique among the
    live records can still have several soft-deleted records with the same name; without this
    filter the dedup guard would raise for such a name. The filter lives in the query document
    (server-side), so this guards the document rather than a mocked response.
    """
    printed = print_ast(_QUERY)
    assert "removed: {equalTo: false}" in printed


def test_query_latch_records_by_name_offline_empty_input_returns_empty_without_query(
    mocker: MockerFixture,
) -> None:
    """An empty name list must return `{}` without issuing a query."""
    execute_mock = mocker.patch("fglatch.registry._registry.execute")

    assert query_latch_records_by_name([], table_id=FAKE_TABLE_ID) == {}
    execute_mock.assert_not_called()


def test_query_latch_records_by_name_offline_warns_on_unknown_column_key(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    """A stored value for an unknown column key must be skipped with a warning, not silently."""
    response = _response(_node(1, "r", {"foo": _valid("hi"), "ghost": _valid("x")}))
    mocker.patch("fglatch.registry._registry.execute", return_value=response)

    with caplog.at_level(logging.WARNING, logger="fglatch.registry._registry"):
        query_latch_records_by_name("r", table_id=FAKE_TABLE_ID)

    assert "ghost" in caplog.text


def test_query_latch_records_by_name_offline_raises_if_table_not_found(
    mocker: MockerFixture,
) -> None:
    """A `null` experiment (nonexistent/forbidden table) must raise TableNotFoundError."""
    mocker.patch("fglatch.registry._registry.execute", return_value={"catalogExperiment": None})

    with pytest.raises(TableNotFoundError, match="Could not retrieve table id=NOPE"):
        query_latch_records_by_name("name_1", table_id="NOPE")


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
    response = _response(
        _node(1, "dup", {"foo": _valid("a")}),
        _node(2, "dup", {"foo": _valid("b")}),
    )
    mocker.patch("fglatch.registry._registry.execute", return_value=response)

    with pytest.raises(ValueError, match="Multiple records named 'dup' found in table"):
        query_latch_records_by_name("dup", table_id=FAKE_TABLE_ID)


def test_query_latch_records_by_name_offline_raises_if_response_cannot_be_validated(
    mocker: MockerFixture,
) -> None:
    """A GQL response of the wrong shape must raise a ValidationError."""
    bad_response: dict[str, Any] = {"catalogExperiment": {"whoops_whats_this": {"nodes": []}}}
    mocker.patch("fglatch.registry._registry.execute", return_value=bad_response)

    with pytest.raises(ValidationError):
        query_latch_records_by_name("r", table_id=FAKE_TABLE_ID)


class MockRecord(LatchRecordModel):
    """
    A fake record for testing.

    Corresponds to `mock-table-1` (id=11730) in the Fulcrum workspace.
    """

    foo: str
    bar: int


def test_from_record_reads_deferred_values_offline(mocker: MockerFixture) -> None:
    """A deferred record (values unprimed) still validates: `from_record` lazy-loads them."""
    mocker.patch(
        "fglatch.registry._registry.execute",
        return_value=_response(_node(1, "r"), with_values=False),
    )
    record = query_latch_records_by_name("r", table_id=FAKE_TABLE_ID, defer_values=True)["r"]
    assert record.get_values(load_if_missing=False) is None

    # `from_record` -> `get_values()` triggers `Record.load()`; mock the SDK's record-level query.
    mocker.patch(
        "latch.registry.record.execute",
        return_value=_load_response({"foo": _valid("hello"), "bar": _valid(42)}),
    )

    validated_record = MockRecord.from_record(record)

    assert validated_record.name == "r"
    assert validated_record.foo == "hello"
    assert validated_record.bar == 42


def test_offline_eager_none_fill_matches_load_for_empty_required_column(
    mocker: MockerFixture,
) -> None:
    """
    Eager `None`-fill of an empty *required* column must equal what `Record.load()` produces.

    Runs the real SDK `load()` on the same missing-required record, so it fails loudly if a future
    `latch` fixes `load()`'s dead-code `InvalidValue("")` branch (which would make the eager path,
    still `None`, diverge from the deferred/`load()` path).
    """
    # Eager: `foo` (required) is missing -> primed as None.
    mocker.patch(
        "fglatch.registry._registry.execute",
        return_value=_response(_node(1, "r", {"bar": _valid(7)})),
    )
    eager = query_latch_records_by_name("r", table_id=FAKE_TABLE_ID)["r"]

    # Record.load() on the same record (foo missing), running the real SDK parse.
    mocker.patch("latch.registry.record.execute", return_value=_load_response({"bar": _valid(7)}))
    fresh = Record("1")
    fresh.load()

    assert eager.get_values(load_if_missing=False) == fresh.get_values()


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
