from collections import Counter
from typing import cast

import gql
from latch.registry.record import Record
from latch.registry.record import _Cache
from latch_sdk_gql import JsonArray
from latch_sdk_gql.execute import execute
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from fglatch.type_aliases import RecordName


class Experiment(BaseModel):
    """The experiment (i.e. Registry table) that a catalog sample belongs to."""

    model_config = ConfigDict(frozen=True)

    id: int


class LatchNode(BaseModel):
    """A single `catalogSample` node: a record's id, name, and owning table id."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    experiment: Experiment


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


def _primed_record(node: LatchNode) -> Record:
    """
    Build a `Record` with its name and table id primed from the query response.

    Args:
        node: A catalog sample node carrying the record's id, name, and table (experiment) id.

    Returns:
        A `Record` whose cache holds the name and table id, so reading either does not trigger a
        network load.
    """
    record = Record(str(node.id))
    cache = _Cache(table_id=str(node.experiment.id), name=node.name)

    # `Record` is a frozen dataclass whose `_cache` field is `init=False`, so neither the
    # constructor nor `dataclasses.replace()` can inject a populated cache. `object.__setattr__` is
    # the sanctioned way to write a field on a frozen dataclass — it is exactly the mechanism a
    # frozen dataclass's own `__post_init__` uses.
    object.__setattr__(record, "_cache", cache)

    return record


def query_latch_records_by_name(
    record_names: str | list[str],
    /,
    *,
    table_id: str,
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

    Raises:
        ValidationError: If the GQL response can't be validated.
        ValueError: If no record is found for a requested name.
        ValueError: If multiple records are found with the same name. (Names should be unique within
            a table, so this should only happen if there are name collisions _across_ Registry
            tables. Requiring a `table_id` is intended to avoid this, and this error is not
            expected to be raised in practice.)
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

    return {node.name: _primed_record(node) for node in nodes}
