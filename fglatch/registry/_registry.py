from typing import cast

import gql
from latch.registry.record import Record
from latch.registry.table import TableNotFoundError
from latch_sdk_gql import JsonArray
from latch_sdk_gql.execute import execute
from pydantic import BaseModel
from pydantic import Field

from fglatch.type_aliases import RecordName

# Scope to one table via `catalogExperiment(id:)` (a shared name can't leak in; a missing/forbidden
# table returns `null`). Filter out soft-deleted records with `removed: {equalTo: false}`: Latch's
# name-uniqueness constraint holds only over live records, but `catalogSamplesByExperimentId`
# returns removed records too, so an unfiltered query can surface several same-named records for a
# name that is unique among the live ones. Only each record's `id`/`name` are fetched; values are
# left to a lazy `get_values()`.
_QUERY = gql.gql("""
    query ($tableId: BigInt!, $sampleNames: [String!]) {
        catalogExperiment(id: $tableId) {
            id
            catalogSamplesByExperimentId(
                filter: {name: {in: $sampleNames}, removed: {equalTo: false}}
            ) {
                nodes {
                    id
                    name
                }
            }
        }
    }
""")


class LatchNode(BaseModel):
    """A record node (`id` and `name`) from the query response."""

    id: str
    name: str


class CatalogSamples(BaseModel):
    """The `catalogSamplesByExperimentId` connection of matching record nodes."""

    nodes: list[LatchNode]


class CatalogExperiment(BaseModel):
    """The queried `catalogExperiment` (i.e. the table)."""

    id: str
    catalog_samples: CatalogSamples = Field(alias="catalogSamplesByExperimentId")


class CatalogSamplesQueryResponse(BaseModel):
    """The GQL response: a `catalogExperiment`, or `null` when the table is unavailable."""

    catalog_experiment: CatalogExperiment | None = Field(alias="catalogExperiment")


def query_latch_records_by_name(
    record_names: str | list[str],
    /,
    *,
    table_id: str,
) -> dict[RecordName, Record]:
    """
    Fetch Latch Registry records by name from a single table.

    The query is scoped to `table_id` server-side, so only records from that table are returned.
    Each returned `Record` has its name and table ID primed, so `get_name()` and `get_table_id()`
    are cache hits (no network request). Column values are not primed: the first `get_values()` on
    a record loads them lazily. `LatchRecordModel.from_record()` reads and validates the values.

    Args:
        record_names: A record name, or a list of record names (duplicates are ignored), in the
            Latch Registry.
        table_id: The ID of the table that contains the records.

    Returns:
        A mapping from record name to the corresponding `Record`. Empty if `record_names` is empty.

    Raises:
        ValidationError: If the GQL response cannot be validated.
        TableNotFoundError: If no table with `table_id` exists in, or is accessible from, the active
            workspace.
        ValueError: If no record is found for a requested name.
        ValueError: If the table contains more than one record with the same name.
    """
    if isinstance(record_names, str):
        record_names = [record_names]

    if len(record_names) == 0:
        return {}

    # `list[str]` satisfies `JsonArray = list[JsonValue]` semantically, but mypy cannot infer it
    # through the mutually-recursive `JsonValue` union; the cast works around that limitation.
    sample_names: JsonArray = cast(JsonArray, record_names)

    data = execute(document=_QUERY, variables={"tableId": table_id, "sampleNames": sample_names})
    response = CatalogSamplesQueryResponse.model_validate(data)

    if response.catalog_experiment is None:
        raise TableNotFoundError(
            f"Could not retrieve table id={table_id}.\n"
            "Check that the table ID is correct and that you can access it in the active workspace."
        )

    record_map: dict[RecordName, Record] = {}
    for node in response.catalog_experiment.catalog_samples.nodes:
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
        # Prime name and table_id from the response so these getters skip a round trip. `table_id`
        # uses `experiment.id` — the same source `Record.load()` reads it from — so a later lazy
        # load leaves it unchanged. Mutating the SDK-internal `Record._cache` (a non-frozen cache on
        # the frozen `Record`) is what `Table.list_records()` does for `name`; the offline priming
        # test guards the field names in CI.
        record._cache.name = name
        record._cache.table_id = response.catalog_experiment.id

        record_map[name] = record

    missing: list[RecordName] = [name for name in record_names if name not in record_map]
    if missing:
        raise ValueError(
            "Could not find records for the queried names:\n"
            + "\n".join(f"No record found with name: {name}" for name in missing)
        )

    return record_map
