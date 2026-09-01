from collections import Counter
from typing import Any
from typing import cast

import gql
from latch.registry.record import NoSuchColumnError
from latch.registry.record import Record
from latch.registry.record import _Cache
from latch.registry.types import Column
from latch.registry.types import InvalidValue
from latch.registry.types import RecordValue
from latch.registry.upstream_types.values import DBValue
from latch.registry.utils import RegistryTransformerException
from latch.registry.utils import to_python_literal
from latch.registry.utils import to_python_type
from latch_sdk_gql import JsonArray
from latch_sdk_gql.execute import execute
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from fglatch.type_aliases import RecordName


class ColumnDefinition(BaseModel):
    """A single column definition: its key and its raw Registry type."""

    model_config = ConfigDict(frozen=True)

    key: str
    # Opaque `DBType`, handed straight to the SDK's `to_python_type` / `to_python_literal` rather
    # than re-modeled here, so we don't couple to the SDK's recursive Registry-type shapes.
    type: Any


class ColumnDefinitions(BaseModel):
    """The column definitions returned for an experiment (table)."""

    model_config = ConfigDict(frozen=True)

    nodes: list[ColumnDefinition]


class ColumnDatum(BaseModel):
    """A single column value for a record: its key and its raw Registry value."""

    model_config = ConfigDict(frozen=True)

    key: str
    # Opaque `DBValue`, handed straight to the SDK's `to_python_literal` (see `ColumnDefinition`).
    data: Any


class ColumnData(BaseModel):
    """The column values returned for a record."""

    model_config = ConfigDict(frozen=True)

    nodes: list[ColumnDatum]


class Experiment(BaseModel):
    """The experiment (i.e. Registry table) that a catalog sample belongs to."""

    model_config = ConfigDict(frozen=True)

    id: int
    column_definitions: ColumnDefinitions | None = Field(
        default=None, alias="catalogExperimentColumnDefinitionsByExperimentId"
    )


class LatchNode(BaseModel):
    """A `catalogSample` node: a record's id, name, table id, and (optionally) its column values."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    experiment: Experiment
    column_data: ColumnData | None = Field(default=None, alias="catalogSampleColumnDataBySampleId")


class CatalogSamples(BaseModel):
    """The `nodes` list returned under `catalogSamples`."""

    model_config = ConfigDict(frozen=True)

    nodes: list[LatchNode]


class CatalogSamplesQueryResponse(BaseModel):
    """The top-level response returned by the records query."""

    model_config = ConfigDict(frozen=True)

    catalog_samples: CatalogSamples = Field(alias="catalogSamples")


_RECORDS_QUERY = gql.gql("""
    query Query($sampleNames: [String!]) {
        catalogSamples(filter: {name: {in: $sampleNames}}) {
            nodes {
                id
                name
                experiment {
                    id
                }
            }
        }
    }
""")
"""Fetch matching records' id, name, and owning table id in a single request."""


_RECORDS_WITH_VALUES_QUERY = gql.gql("""
    query Query($sampleNames: [String!]) {
        catalogSamples(filter: {name: {in: $sampleNames}}) {
            nodes {
                id
                name
                experiment {
                    id
                    catalogExperimentColumnDefinitionsByExperimentId {
                        nodes {
                            type
                            key
                            def
                        }
                    }
                }
                catalogSampleColumnDataBySampleId {
                    nodes {
                        key
                        data
                    }
                }
            }
        }
    }
""")
"""Additionally fetch each record's column definitions and values, to prime them without a load."""


def _record_with_cache(record_id: str, cache: _Cache) -> Record:
    """
    Create a `Record` and attach a pre-populated cache to it.

    Args:
        record_id: The record's unique id.
        cache: The cache to attach to the record.

    Returns:
        A `Record` whose `_cache` is the given cache.
    """
    record = Record(record_id)

    # `Record` is a frozen dataclass whose `_cache` field is `init=False`, so neither the
    # constructor nor `dataclasses.replace()` can inject a populated cache. `object.__setattr__` is
    # the sanctioned way to write a field on a frozen dataclass — it is exactly the mechanism a
    # frozen dataclass's own `__post_init__` uses.
    object.__setattr__(record, "_cache", cache)

    return record


def _primed_record(node: LatchNode) -> Record:
    """
    Build a `Record` with its name and table id primed from the query response.

    Args:
        node: A catalog sample node carrying the record's id, name, and table (experiment) id.

    Returns:
        A `Record` whose cache holds the name and table id, so reading either does not trigger a
        network load.
    """
    cache = _Cache(table_id=str(node.experiment.id), name=node.name)
    return _record_with_cache(str(node.id), cache)


def _records_with_primed_values(nodes: list[LatchNode]) -> dict[RecordName, Record]:
    """
    Build records with their values primed, collecting any per-record conversion failures.

    Args:
        nodes: Catalog sample nodes fetched by the values query (i.e. including column definitions
            and data).

    Returns:
        A mapping from record name to a `Record` whose name, table id, columns, and values are all
        primed from the query.

    Raises:
        ValueError: If one or more records' values cannot be converted to their Python types. All
            failures are collected and reported together.
    """
    records: dict[RecordName, Record] = {}
    errs: list[str] = []
    for node in nodes:
        try:
            cache = _cache_from_catalog_sample(node)
        except (RegistryTransformerException, NoSuchColumnError) as error:
            errs.append(f"{node.name} (id={node.id}): {error}")
            continue

        records[node.name] = _record_with_cache(str(node.id), cache)

    if errs:
        raise ValueError("Failed to load values for records:\n" + "\n".join(errs))

    return records


def _cache_from_catalog_sample(node: LatchNode) -> _Cache:
    """
    Build a fully-populated `_Cache` (name, table id, columns, values) from a catalog sample.

    This mirrors the transform in `latch.registry.record.Record.load()` so a primed record is
    indistinguishable from one populated by a network `load()`. The SDK's own `to_python_type` and
    `to_python_literal` are reused, so the value conversion cannot drift from the SDK's.

    Args:
        node: A catalog sample node that includes column definitions and column data (i.e. one
            fetched by the values query).

    Returns:
        A `_Cache` populated with the record's name, table id, columns, and converted values.

    Raises:
        RuntimeError: If the node lacks column definitions or column data (i.e. it came from the
            light query rather than the values query).
        NoSuchColumnError: If a column datum references a column that has no definition.
        RegistryTransformerException: If a value cannot be converted to its Python type.
    """
    if node.experiment.column_definitions is None or node.column_data is None:
        raise RuntimeError(
            "catalog sample is missing column definitions or data; "
            "it must be fetched with the values query"
        )

    columns: dict[str, Column] = {
        defn.key: Column(defn.key, to_python_type(defn.type["type"]), defn.type)
        for defn in node.experiment.column_definitions.nodes
    }

    column_values: dict[str, DBValue] = {datum.key: datum.data for datum in node.column_data.nodes}

    values: dict[str, RecordValue] = {}
    for key, db_value in column_values.items():
        column = columns.get(key)
        if column is None:
            raise NoSuchColumnError(key)

        values[key] = to_python_literal(db_value, column.upstream_type["type"])

    for key, column in columns.items():
        if key in values:
            continue

        # NB: this mirrors a quirk in `Record.load()` (record.py:200-204): it sets
        # `InvalidValue("")` for a missing required value and then unconditionally overwrites it
        # with `None`, so every missing value ends up `None`. We reproduce it exactly so a primed
        # record matches a lazily-loaded one rather than silently diverging.
        if not column.upstream_type["allowEmpty"]:
            values[key] = InvalidValue("")

        values[key] = None

    return _Cache(
        table_id=str(node.experiment.id),
        name=node.name,
        columns=columns,
        values=values,
    )


def query_latch_records_by_name(
    record_names: str | list[str],
    /,
    *,
    table_id: str,
    load_values: bool = False,
) -> dict[RecordName, Record]:
    """
    Fetch a set of Latch Registry records by their names.

    Records are fetched across all Registry tables and then filtered to `table_id`. Each returned
    record has its name and table id primed from the query, so those can be read without an
    additional per-record network request.

    Args:
        record_names: A record name or a list of record names in the Latch Registry.
        table_id: The ID of the table to fetch records from. Only records from this table are
            returned.
        load_values: If True, fetch each record's column values in the same request and prime them
            onto the returned records, so reading values does not trigger a per-record load. If
            False (the default), values are loaded lazily on first access.

    Raises:
        ValidationError: If the GQL response can't be validated.
        ValueError: If no record is found for a requested name.
        ValueError: If multiple records are found with the same name. (Names should be unique within
            a table, so this should only happen if there are name collisions _across_ Registry
            tables. Requiring a `table_id` is intended to avoid this, and this error is not
            expected to be raised in practice.)
        ValueError: If `load_values` is True and one or more records' values cannot be converted to
            their Python types.
    """
    if isinstance(record_names, str):
        record_names = [record_names]

    # The `variables` argument to `execute()` is typed to receive a dict with `JsonValue` values.
    # `list[str]` matches `JsonValue` semantically, but mypy has limitations with recursive type
    # aliases containing forward references. In this case, it can't infer that `list[str]` satisfies
    # the `JsonArray = list[JsonValue]` member of the `JsonValue` union since `JsonValue` and
    # `JsonArray` circularly reference each other. The cast works around this limitation.
    sample_names: JsonArray = cast(JsonArray, record_names)

    query = _RECORDS_WITH_VALUES_QUERY if load_values else _RECORDS_QUERY
    data = execute(
        document=query,
        variables={"sampleNames": sample_names},
    )

    response = CatalogSamplesQueryResponse.model_validate(data)

    # Keep only the records in the requested table. Filtering on the table id returned by the query
    # avoids a per-record network load to resolve each record's table.
    nodes: list[LatchNode] = [
        node for node in response.catalog_samples.nodes if str(node.experiment.id) == table_id
    ]

    name_counts: Counter[RecordName] = Counter(node.name for node in nodes)

    errs: list[str] = []
    for record_name in record_names:
        count: int = name_counts[record_name]
        if count == 0:
            errs.append(f"No record found with name: {record_name}")
        elif count > 1:
            errs.append(f"Duplicate record name: {record_name} (n={count})")

    if errs:
        raise ValueError("Could not find unique records for queried names" + "\n".join(errs))

    if load_values:
        return _records_with_primed_values(nodes)

    return {node.name: _primed_record(node) for node in nodes}
