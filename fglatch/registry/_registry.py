from collections import Counter
from collections.abc import Iterable
from typing import Any
from typing import cast

import gql
from dateutil.parser import isoparse
from latch.registry.record import NoSuchColumnError
from latch.registry.record import Record
from latch.registry.record import _Cache
from latch.registry.types import Column
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


class _FrozenModel(BaseModel):
    """A frozen Pydantic model that can be used as a base class for other models."""

    model_config = ConfigDict(frozen=True)


class ColumnDefinition(_FrozenModel):
    """A single column definition: its key and its raw Registry type."""

    key: str
    type: Any  # Opaque `DBType`, passed to the SDK's `to_python_type` / `to_python_literal`.


class ColumnDefinitions(_FrozenModel):
    """The column definitions returned for an experiment (table)."""

    nodes: list[ColumnDefinition]


class ColumnDatum(_FrozenModel):
    """A single column value for a record: its key and its raw Registry value."""

    key: str
    data: Any  # Opaque `DBValue`, passed to the SDK's `to_python_literal`.


class ColumnData(_FrozenModel):
    """The column values returned for a record."""

    nodes: list[ColumnDatum]


class CatalogEvent(_FrozenModel):
    """A single catalog event for a record (e.g. an update), carrying its timestamp."""

    time: str


class CatalogEvents(_FrozenModel):
    """The most recent catalog events for a record."""

    nodes: list[CatalogEvent]


class Experiment(_FrozenModel):
    """The experiment (i.e. Registry table) that a catalog sample belongs to."""

    id: int
    column_definitions: ColumnDefinitions | None = Field(
        default=None,
        alias="catalogExperimentColumnDefinitionsByExperimentId",
    )


class LatchNode(_FrozenModel):
    """A `catalogSample` node: id, name, table id, and (optionally) values and timestamps."""

    id: int
    name: str
    experiment: Experiment
    column_data: ColumnData | None = Field(default=None, alias="catalogSampleColumnDataBySampleId")
    creation_time: str | None = Field(default=None, alias="creationTime")
    events: CatalogEvents | None = Field(default=None, alias="catalogEventsBySampleId")

    def to_cache(self) -> _Cache:
        """
        Build this record's `_Cache` from whatever the node contains.

        Mirrors the transform in `latch.registry.record.Record.load()`. A preloaded record built
        from our query is consistent with one populated by `Record.load()`.

        If the node includes column data (i.e. it was fetched by the values query), its columns and
        converted values are built too; otherwise only the name, table id, and timestamps are set
        and values remain to be lazily loaded with `Record.get_values()`.

        Returns:
            A `_Cache` with the record's name and table id. Timestamps, columns, and converted
            values are included if the node carries them.

        Raises:
            RuntimeError: If the node has column definitions or data but not both (a malformed
                values response).
            NoSuchColumnError: If a column datum references a column that has no definition.
            RegistryTransformerException: If a value cannot be converted to its Python type.
        """
        creation_time = isoparse(self.creation_time) if self.creation_time is not None else None
        last_updated = creation_time
        if self.events is not None and len(self.events.nodes) > 0:
            last_updated = isoparse(self.events.nodes[0].time)

        columns: dict[str, Column] | None = None
        values: dict[str, RecordValue] | None = None
        if self.column_data is not None or self.experiment.column_definitions is not None:
            columns, values = self._columns_and_values()

        return _Cache(
            table_id=str(self.experiment.id),
            name=self.name,
            creation_time=creation_time,
            last_updated=last_updated,
            columns=columns,
            values=values,
        )

    def to_record(self) -> Record:
        """
        Build a `Record` with this node's data preloaded onto its cache.

        Returns:
            A `Record` with preloaded cache, so the corresponding getters do not trigger a network
            load. Columns and values are preloaded when the node carries them (see `to_cache`).
        """
        record = Record(str(self.id))
        object.__setattr__(record, "_cache", self.to_cache())

        return record

    def _columns_and_values(self) -> tuple[dict[str, Column], dict[str, RecordValue]]:
        """
        Build the record's columns and converted values, mirroring `Record.load()`.

        Returns:
            A `(columns, values)` pair keyed by column key.

        Raises:
            RuntimeError: If the node lacks column definitions or data.
            NoSuchColumnError: If a column datum references a column that has no definition.
            RegistryTransformerException: If a value cannot be converted to its Python type.
        """
        if self.experiment.column_definitions is None or self.column_data is None:
            raise RuntimeError(
                "catalog sample is missing column definitions or data; "
                "it must be fetched with the values query"
            )

        columns: dict[str, Column] = {
            defn.key: Column(defn.key, to_python_type(defn.type["type"]), defn.type)
            for defn in self.experiment.column_definitions.nodes
        }

        column_values: dict[str, DBValue] = {
            datum.key: datum.data for datum in self.column_data.nodes
        }

        values: dict[str, RecordValue] = {}
        for key, db_value in column_values.items():
            column = columns.get(key)
            if column is None:
                raise NoSuchColumnError(key)

            values[key] = to_python_literal(db_value, column.upstream_type["type"])

        for key in columns:
            if key in values:
                continue

            # A column with no datum resolves to `None`, matching `Record.load()`: it sets
            # `InvalidValue("")` for a missing required value, then unconditionally overwrites it
            # with `None` (record.py:200-204), so every missing value ends up `None`.
            values[key] = None

        return columns, values


class CatalogSamples(_FrozenModel):
    """The `nodes` list returned under `catalogSamples`."""

    nodes: list[LatchNode]


class CatalogSamplesQueryResponse(_FrozenModel):
    """The top-level response returned by the records query."""

    catalog_samples: CatalogSamples = Field(alias="catalogSamples")


# `removed: {equalTo: false}` excludes soft-deleted records: Latch's name-uniqueness constraint
# holds only over live records, so an unfiltered query can return several same-named records for a
# name that is unique among the live ones (a spurious duplicate). `removed` is NOT NULL (the SDK
# filters experiments the same way, `registry/project.py` `condition: {removed: false}`), so exact
# `equalTo: false` keeps every live record.
_RECORDS_QUERY = gql.gql("""
    query Query($sampleNames: [String!]) {
        catalogSamples(filter: {name: {in: $sampleNames}, removed: {equalTo: false}}) {
            nodes {
                id
                name
                creationTime
                catalogEventsBySampleId(orderBy: TIME_DESC, first: 1) {
                    nodes {
                        time
                    }
                }
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
"""Fetch matching records with their column definitions and values in a single request."""


_RECORDS_BY_ID_QUERY = gql.gql("""
    query Query($ids: [BigInt!]) {
        catalogSamples(filter: {id: {in: $ids}}) {
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
"""Fetch id, name, and owning table id for a set of records identified by id."""


def _preload_linked_record_names(records: Iterable[Record]) -> None:
    """
    Preload the names of records linked from `records`' values, in a single query.

    A link-column value is a `Record` with only its id populated. This resolves all linked records
    at once and preloads each one's name, so reading it makes no per-record network request.

    Args:
        records: The records whose values may contain linked records.
    """
    # A linked id can appear in several cells; `to_python_literal` mints a fresh Record for each, so
    # every instance of an id is collected and primed (not just the last one seen).
    linked: dict[str, list[Record]] = {}
    for record in records:
        values = record.get_values(load_if_missing=False)
        if values is None:
            continue

        for value in values.values():
            for item in value if isinstance(value, list) else (value,):
                if isinstance(item, Record):
                    linked.setdefault(item.id, []).append(item)

    if not linked:
        return

    data = execute(
        document=_RECORDS_BY_ID_QUERY,
        variables={"ids": cast(JsonArray, list(linked))},
    )
    response = CatalogSamplesQueryResponse.model_validate(data)
    for node in response.catalog_samples.nodes:
        # Instances of one id share a cache: same record, so identical data, and a later lazy load
        # through any instance repopulates the shared cache for all of them.
        cache = node.to_cache()
        for record in linked.get(str(node.id), []):
            object.__setattr__(record, "_cache", cache)


def query_latch_records_by_name(
    record_names: str | list[str],
    /,
    *,
    table_id: str,
) -> dict[RecordName, Record]:
    """
    Fetch a set of Latch Registry records by their names.

    Records are fetched across all Registry tables and then filtered to `table_id`. Each returned
    record is fully preloaded from the query — its name, table id, columns, and values — so reading
    them makes no additional per-record network request. Linked-record cells additionally have their
    names resolved in one further query, so reading a linked record's name needs no network request.

    Args:
        record_names: A record name or a list of record names in the Latch Registry.
        table_id: The ID of the table to fetch records from. Only records from this table are
            returned.

    Raises:
        ValidationError: If the GQL response can't be validated.
        ValueError: If no record is found for a requested name.
        ValueError: If multiple records are found with the same name. (Names should be unique within
            a table, so this should only happen if there are name collisions _across_ Registry
            tables. Requiring a `table_id` is intended to avoid this, and this error is not
            expected to be raised in practice.)
        ValueError: If one or more records' values cannot be converted to their Python types.
        RuntimeError: If a record's values response is malformed (missing column definitions or
            data). Not expected in practice: the by-name query always fetches both.
    """
    if isinstance(record_names, str):
        record_names = [record_names]

    # The `variables` argument to `execute()` is typed to receive a dict with `JsonValue` values.
    # `list[str]` matches `JsonValue` semantically, but mypy has limitations with recursive type
    # aliases containing forward references. In this case, it can't infer that `list[str]` satisfies
    # the `JsonArray = list[JsonValue]` member of the `JsonValue` union since `JsonValue` and
    # `JsonArray` circularly reference each other. The cast works around this limitation.
    sample_names: JsonArray = cast(JsonArray, record_names)

    data = execute(
        document=_RECORDS_QUERY,
        variables={"sampleNames": sample_names},
    )

    response = CatalogSamplesQueryResponse.model_validate(data)

    # Filter to records from the specified table.
    nodes: list[LatchNode] = [
        node for node in response.catalog_samples.nodes if str(node.experiment.id) == table_id
    ]

    name_counts: Counter[RecordName] = Counter(node.name for node in nodes)

    query_errs: list[str] = []
    for record_name in record_names:
        count: int = name_counts[record_name]
        if count == 0:
            query_errs.append(f"No record found with name: {record_name}")
        elif count > 1:
            query_errs.append(f"Duplicate record name: {record_name} (n={count})")

    # Build each record, preloading its cache from the node.
    records: dict[RecordName, Record] = {}
    value_errs: list[str] = []
    for node in nodes:
        try:
            records[node.name] = node.to_record()
        except (RegistryTransformerException, NoSuchColumnError) as error:
            value_errs.append(f"{node.name} (id={node.id}): {error}")

    if query_errs or value_errs:
        raise ValueError("Could not query records by name:\n" + "\n".join(query_errs + value_errs))

    _preload_linked_record_names(records.values())

    return records
