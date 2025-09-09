from collections import Counter
from typing import cast

import gql
from latch.registry.record import Record
from latch_sdk_gql import JsonArray
from latch_sdk_gql.execute import execute
from pydantic import BaseModel
from pydantic import Field

from fglatch.type_aliases import RecordName


class LatchNode(BaseModel):
    """The gql query below returns {'catalogSamples': {'nodes': [{'id': int}]}}."""

    id: int


class CatalogSamples(BaseModel):
    """The gql query below returns {'catalogSamples': {'nodes': [{'id': int}]}}."""

    nodes: list[LatchNode]


class CatalogSamplesQueryResponse(BaseModel):
    """The gql query below returns {'catalogSamples': {'nodes': [{'id': int}]}}."""

    catalog_samples: CatalogSamples = Field(alias="catalogSamples")


def query_latch_records_by_name(
    record_names: str | list[str],
    /,
    *,
    table_id: str,
) -> dict[RecordName, Record]:
    """
    Fetch a set of Latch Registry records by their names.

    By default, the query is performed across *all* tables in the Registry. If a table ID is
    provided, only records from the specified table will be included in the response.

    Args:
        record_names: A record name or a list of record names in the Latch Registry.
        table_id: An optional table ID. If provided, only records from this table will be included
            in the returned dictionary.

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
        document=gql.gql("""
            query Query($sampleNames:[String!]) {
                catalogSamples(filter: {name: {in: $sampleNames}}) {
                    nodes {
                    id
                    name
                    }
                }
            }
            """),
        variables={"sampleNames": sample_names},
    )

    response = CatalogSamplesQueryResponse.model_validate(data)
    records: list[Record] = [Record(str(k.id)) for k in response.catalog_samples.nodes]

    # Filter to records from the specified table.
    records = [r for r in records if r.get_table_id() == table_id]

    name_counts: Counter[RecordName] = Counter(record.get_name() for record in records)

    errs: list[str] = []
    for record_name in record_names:
        count: int = name_counts[record_name]
        if count == 0:
            errs.append(f"No record found with name: {record_name}")
        elif count > 1:
            errs.append(f"Duplicate record name: {record_name} (n={count})")

    if errs:
        raise ValueError("Could not find unique records for queried names" + "\n".join(errs))

    record_map: dict[RecordName, Record] = {record.get_name(): record for record in records}

    return record_map
