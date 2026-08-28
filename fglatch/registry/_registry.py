import logging
from typing import Any
from typing import cast

import gql
from latch.registry.record import Record
from latch.registry.table import TableNotFoundError
from latch.registry.types import Column
from latch.registry.types import RecordValue
from latch.registry.upstream_types.types import DBType
from latch.registry.upstream_types.values import DBValue
from latch.registry.utils import to_python_literal
from latch.registry.utils import to_python_type
from latch_sdk_gql import JsonArray
from latch_sdk_gql.execute import execute
from pydantic import BaseModel
from pydantic import Field

from fglatch.type_aliases import RecordName

logger = logging.getLogger(__name__)

# Scope to one table via `catalogExperiment(id:)` (a shared name can't leak in; a missing/forbidden
# table returns `null`). Filter out soft-deleted records with `removed: {equalTo: false}`: Latch's
# name-uniqueness constraint holds only over live records, but `catalogSamplesByExperimentId`
# returns removed records too, so an unfiltered query can surface several same-named records for a
# name that is unique among the live ones. `@include(if: $withValues)` fetches column defs + inline
# values only when wanted.
_QUERY = gql.gql("""
    query ($tableId: BigInt!, $sampleNames: [String!], $withValues: Boolean!) {
        catalogExperiment(id: $tableId) {
            id
            catalogExperimentColumnDefinitionsByExperimentId @include(if: $withValues) {
                nodes {
                    key
                    type
                }
            }
            catalogSamplesByExperimentId(
                filter: {name: {in: $sampleNames}, removed: {equalTo: false}}
            ) {
                nodes {
                    id
                    name
                    catalogSampleColumnDataBySampleId @include(if: $withValues) {
                        nodes {
                            key
                            data
                        }
                    }
                }
            }
        }
    }
""")


class ColumnDatum(BaseModel):
    """A stored column value on a record (`data` is an opaque Registry value, not validated)."""

    key: str
    # A `DBValue` is a dict for primitives/unions but a bare list for array-typed columns; both are
    # validated only structurally here (cast to the SDK's opaque `DBValue` at the parse call).
    data: dict[str, Any] | list[Any]


class ColumnData(BaseModel):
    """The `catalogSampleColumnDataBySampleId` connection of a record's stored values."""

    nodes: list[ColumnDatum]


class ColumnDefinition(BaseModel):
    """A column definition (`type` is an opaque Registry type, not validated here)."""

    key: str
    type: dict[str, Any]


class ColumnDefinitions(BaseModel):
    """The `catalogExperimentColumnDefinitionsByExperimentId` connection of a table's columns."""

    nodes: list[ColumnDefinition]


class LatchNode(BaseModel):
    """A record node (`id` and `name`, plus inline `column_data` when values are requested)."""

    id: str
    name: str
    column_data: ColumnData | None = Field(default=None, alias="catalogSampleColumnDataBySampleId")


class CatalogSamples(BaseModel):
    """The `catalogSamplesByExperimentId` connection of matching record nodes."""

    nodes: list[LatchNode]


class CatalogExperiment(BaseModel):
    """The queried `catalogExperiment` (i.e. the table), with its columns when values are wanted."""

    id: str
    column_definitions: ColumnDefinitions | None = Field(
        default=None, alias="catalogExperimentColumnDefinitionsByExperimentId"
    )
    catalog_samples: CatalogSamples = Field(alias="catalogSamplesByExperimentId")


class CatalogSamplesQueryResponse(BaseModel):
    """The GQL response: a `catalogExperiment`, or `null` when the table is unavailable."""

    catalog_experiment: CatalogExperiment | None = Field(alias="catalogExperiment")


def query_latch_records_by_name(
    record_names: str | list[str],
    /,
    *,
    table_id: str,
    defer_values: bool = False,
) -> dict[RecordName, Record]:
    """
    Fetch Latch Registry records by name from a single table.

    The query is scoped to `table_id` server-side, so only records from that table are returned.
    Each returned `Record` has its name, table ID, columns, and values primed to match what
    `Record.load()` would produce — fetched inline and parsed exactly as `load()` does (no
    validation), so `get_values()` makes no further network request. `from_record()` still validates
    the values. (`creation_time`/`last_updated` are not primed, so those getters still load.)

    Pass `defer_values=True` to skip fetching values (e.g. when a record is only used for its id, to
    link to it); the first `get_values()` then loads them lazily. Note the eager and deferred paths
    handle a stale column key differently: eager skips it with a warning, whereas the deferred
    `load()` raises `NoSuchColumnError`.

    Args:
        record_names: A record name, or a list of record names (duplicates are ignored), in the
            Latch Registry.
        table_id: The ID of the table that contains the records.
        defer_values: If True, do not fetch or prime column values; `get_values()` loads them lazily
            on first access. Defaults to False (values are primed).

    Returns:
        A mapping from record name to the corresponding `Record`. Empty if `record_names` is empty.

    Raises:
        ValidationError: If the GQL response cannot be validated.
        TableNotFoundError: If no table with `table_id` exists in, or is accessible from, the active
            workspace.
        ValueError: If no record is found for a requested name.
        ValueError: If the table contains more than one record with the same name.
        ValueError: If a stored value fails to parse (raised at query time in the default path; a
            `RegistryTransformerException`, which subclasses `ValueError`).
    """
    if isinstance(record_names, str):
        record_names = [record_names]

    if len(record_names) == 0:
        return {}

    # `list[str]` satisfies `JsonArray = list[JsonValue]` semantically, but mypy cannot infer it
    # through the mutually-recursive `JsonValue` union; the cast works around that limitation.
    sample_names: JsonArray = cast(JsonArray, record_names)

    data = execute(
        document=_QUERY,
        variables={
            "tableId": table_id,
            "sampleNames": sample_names,
            "withValues": not defer_values,
        },
    )
    response = CatalogSamplesQueryResponse.model_validate(data)

    experiment = response.catalog_experiment
    if experiment is None:
        raise TableNotFoundError(
            f"Could not retrieve table id={table_id}.\n"
            "Check that the table ID is correct and that you can access it in the active workspace."
        )

    # `withValues=True` selects the column definitions; raise (not `assert`, for `-O`) if absent.
    # All records then share one read-only `columns` dict (do not mutate a record's columns).
    columns: dict[str, Column] | None = None
    if not defer_values:
        if experiment.column_definitions is None:
            raise RuntimeError(
                "Column definitions missing from the response while fetching values."
            )
        columns = _columns_from_definitions(experiment.column_definitions)

    record_map: dict[RecordName, Record] = {}
    for node in experiment.catalog_samples.nodes:
        name: RecordName = node.name
        if name in record_map:
            # Names are unique among the live records in a table by Latch's constraint (the query
            # filters out removed records). This guards data integrity so a violation raises rather
            # than silently overwriting one record with another.
            raise ValueError(
                f"Multiple records named {name!r} found in table id={table_id}. "
                "Record names must be unique within a table."
            )

        record = Record(node.id)
        # Prime `_cache` directly (the SDK-internal idiom `Table.list_records()` uses; the offline
        # tests guard the field names). `table_id` uses `experiment.id` — the value `Record.load()`
        # sets — so a later lazy load leaves it unchanged.
        record._cache.name = name
        record._cache.table_id = experiment.id
        if columns is not None:
            record._cache.columns = columns
            record._cache.values = _record_values(node, columns)

        record_map[name] = record

    missing: list[RecordName] = [name for name in record_names if name not in record_map]
    if missing:
        raise ValueError(
            "Could not find records for the queried names:\n"
            + "\n".join(f"No record found with name: {name}" for name in missing)
        )

    return record_map


def _columns_from_definitions(definitions: ColumnDefinitions) -> dict[str, Column]:
    """Build the table's columns from the response, as `Record.load()` does (no validation)."""
    columns: dict[str, Column] = {}
    for definition in definitions.nodes:
        db_type = cast(DBType, definition.type)
        columns[definition.key] = Column(definition.key, to_python_type(db_type["type"]), db_type)
    return columns


def _record_values(node: LatchNode, columns: dict[str, Column]) -> dict[str, RecordValue]:
    """
    Parse a record's inline column values, mirroring `Record.load()`.

    Missing columns are set to `None` — matching `load()`'s *observed* behaviour (its
    `InvalidValue("")` branch for a missing required column is dead code, overwritten by `None`),
    which differs from `Table.list_records()`. A datum whose key has no column definition is skipped
    with a warning (not raised as `load()` does), so a stale *key* can't fail the batch; a stored
    *value* that fails to parse still raises via `to_python_literal`, exactly as `load()`.
    """
    if node.column_data is None:  # `withValues=True` should have selected the inline data
        raise RuntimeError(
            f"Inline column data missing for record id={node.id} while fetching values."
        )
    values: dict[str, RecordValue] = {}
    for datum in node.column_data.nodes:
        column = columns.get(datum.key)
        if column is None:
            logger.warning("Skipping unknown column key %r on record id=%s.", datum.key, node.id)
            continue
        # `datum.data` is dict-validated by pydantic; cast to the SDK's opaque `DBValue`.
        values[column.key] = to_python_literal(
            cast(DBValue, datum.data), column.upstream_type["type"]
        )
    for column in columns.values():
        values.setdefault(column.key, None)
    return values
