from collections import Counter

import gql
from latch.registry.record import Record
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


def query_latch_records_by_name(record_names: str | list[str]) -> dict[RecordName, Record]:
    """
    Fetch a set of Latch Registry records by their names.

    NOTE: the query is performed across *all* tables in the Registry.

    Args:
        record_names: A list of record names in the Latch Registry.

    Raises:
        ValidationError: If the GQL response can't be validated.
        ValueError: If no record is found for a requested name.
        ValueError: If multiple records are found with the same name. (This may happen if there are
            name collisions _across_ Registry tables.)
    """
    if isinstance(record_names, str):
        record_names = [record_names]

    data = execute(
        gql.gql("""
            query Query($sampleNames:[String!]) {
                catalogSamples(filter: {name: {in: $sampleNames}}) {
                    nodes {
                    id
                    name
                    }
                }
            }
            """),
        {"sampleNames": record_names},
    )

    response = CatalogSamplesQueryResponse.model_validate(data)
    records = [Record(str(k.id)) for k in response.catalog_samples.nodes]

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

    record_map = {record.get_name(): record for record in records}

    return record_map
