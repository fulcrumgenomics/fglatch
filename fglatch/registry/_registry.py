from collections import Counter
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from typing import Any
from typing import cast

import gql
from dateutil.parser import isoparse
from latch.registry.record import NoSuchColumnError
from latch.registry.record import Record
from latch.registry.record import _Cache
from latch.registry.table import Table
from latch.registry.types import Column
from latch.registry.types import RecordValue
from latch.registry.upstream_types.values import DBValue
from latch.registry.utils import RegistryTransformerException
from latch.registry.utils import to_python_literal
from latch.registry.utils import to_python_type
from latch.types.directory import LatchDir
from latch.types.file import LatchFile
from latch.types.utils import is_absolute_node_path
from latch.types.utils import old_style_path
from latch_sdk_gql import JsonArray
from latch_sdk_gql import JsonValue
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


def _format_node_path(node_raw_path: str | None, owner: str | None) -> str | None:
    """
    Format an `(ldataGetPath, ldataOwner)` pair into a readable path, as `format_path` does.

    We format node paths ourselves because `latch`'s `format_path` welds the network fetch to the
    formatting and exposes no pure helper to import, and there is no upstream path to factor one
    out. This reproduces the reachable cases of `format_path`'s cascade (the parity test is the
    drift guard); `_resolve_node_paths` batches the per-id fetch `format_path` does one at a time.

    Returns None when the pair cannot be formatted, so callers omit the id and the cell keeps its
    raw node path. `format_path` also has `mount_gcp`/`mount_azure` branches, but the unanchored
    `old_style_path` regex makes its `mount` alternative shadow them, leaving `mount` and
    `account_root` as the only reachable shapes; the parity test against `format_path` guards this
    if the SDK's regex ever changes.
    """
    if node_raw_path is None:
        return None

    match = old_style_path.match(node_raw_path)
    if match is None:
        return None

    parts = node_raw_path.split("/")
    key = "/".join(parts[2:])

    if match["mount"] is not None:
        return f"latch://{parts[1]}.mount/{key}"

    # Not a mount* form, so `match` is account_root (the only other alternative); needs an owner.
    if owner is None:
        return None

    return f"latch://{owner}.account/{key}"


def _resolve_node_paths(node_ids: Iterable[str], *, chunk_size: int = 1000) -> dict[str, str]:
    """
    Resolve `latch://<id>.node` node ids to readable paths, batched.

    Deduplicates the ids, then issues one aliased GraphQL query per `chunk_size` ids (each id
    contributes an aliased `ldataGetPath` + `ldataOwner`) and applies the same local formatting as
    `latch.types.utils.format_path`. This replaces one network round-trip per id with one per chunk.

    A node that resolves to null (e.g. a deleted node) is omitted, so callers can fall back to the
    raw path. A chunk whose query errors does not stop the others: every chunk failure is collected
    and raised together at the end, so one call surfaces all of them.

    Args:
        node_ids: The node ids to resolve (the `<id>` in `latch://<id>.node`).
        chunk_size: The number of ids resolved per GraphQL query.

    Returns:
        A mapping from node id to readable path, omitting ids that resolve to null.

    Raises:
        ValueError: If `chunk_size` is less than 1.
        RuntimeError: If any chunk's query fails; the message aggregates every chunk failure.
    """
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")

    unique_ids: list[str] = list(dict.fromkeys(node_ids))

    resolved: dict[str, str] = {}
    errors: list[str] = []
    for start in range(0, len(unique_ids), chunk_size):
        chunk = unique_ids[start : start + chunk_size]

        params = ", ".join(f"$id{i}: BigInt!" for i in range(len(chunk)))
        aliases = "\n".join(
            f"  p{i}: ldataGetPath(argNodeId: $id{i})  o{i}: ldataOwner(argNodeId: $id{i})"
            for i in range(len(chunk))
        )
        document = gql.gql(f"query ResolveNodePaths({params}) {{\n{aliases}\n}}")
        variables: dict[str, JsonValue] = {f"id{i}": node_id for i, node_id in enumerate(chunk)}

        # Collect a chunk's failure and keep going, so all failures surface in one raised error.
        try:
            data = execute(document=document, variables=variables)
        except Exception as error:
            errors.append(f"{len(chunk)} node id(s) starting at {chunk[0]!r}: {error}")
            continue

        for i, node_id in enumerate(chunk):
            path = _format_node_path(data[f"p{i}"], data[f"o{i}"])
            if path is not None:
                resolved[node_id] = path

    if errors:
        raise RuntimeError(
            f"Failed to resolve node paths for {len(errors)} chunk(s):\n" + "\n".join(errors)
        )

    return resolved


def _rewrite_node_path(value: Any, node_paths: Mapping[str, str]) -> Any:
    """Rebuild a file/dir node-path cell from `node_paths`; return non-file values unchanged."""
    if not isinstance(value, (LatchFile, LatchDir)) or value.remote_path is None:
        return value
    match = is_absolute_node_path.match(value.remote_path)
    if match is None:
        return value
    path = node_paths.get(match.group("node_id"))
    if path is None:
        return value
    # Registry cells carry only a remote path, so reconstructing from it preserves the whole cell.
    return type(value)(path)


def _collect_file_node_ids(records: Iterable[Record]) -> list[str]:
    """The distinct file/dir node ids referenced by `records`' file cells, in first-seen order."""
    node_ids: dict[str, None] = {}  # dict as an ordered set
    for record in records:
        values = record.get_values(load_if_missing=False)
        if values is None:
            continue
        for value in values.values():
            for item in value if isinstance(value, list) else (value,):
                if isinstance(item, (LatchFile, LatchDir)) and item.remote_path is not None:
                    # Mirror format_path's gate: it round-trips only on a bare latch://<id>.node.
                    match = is_absolute_node_path.match(item.remote_path)
                    if match is not None:
                        node_ids[match.group("node_id")] = None
    return list(node_ids)


def _preload_file_paths(records: Iterable[Record], *, chunk_size: int = 1000) -> None:
    """Resolve every file/dir node path in `records`' values and rewrite the cells in place."""
    records = list(records)
    node_ids = _collect_file_node_ids(records)
    if not node_ids:
        return

    node_paths = _resolve_node_paths(node_ids, chunk_size=chunk_size)
    for record in records:
        values = record.get_values(load_if_missing=False)
        if values is None:
            continue
        for key, value in values.items():
            if isinstance(value, list):
                values[key] = [_rewrite_node_path(item, node_paths) for item in value]
            else:
                values[key] = _rewrite_node_path(value, node_paths)


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
    them makes no additional per-record network request. Linked-record names and file/dir readable
    paths are additionally resolved in batched follow-up queries, so a downstream serializer makes
    no per-cell network request.

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
        RuntimeError: If a file/dir node-path query fails while resolving readable paths.
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
    _preload_file_paths(records.values())

    return records


def fetch_table_records(table_id: str, *, page_size: int = 100) -> Iterator[Record]:
    """
    Stream every record in a table, each with linked names and file/dir paths preloaded.

    Enumerates the table one page at a time; for each page, resolves the two remaining per-cell
    round-trip sources — linked-record names and file/dir readable paths — and installs them onto
    the records' caches before yielding them, so a downstream `from_record`/serializer makes no
    per-cell network request for resolvable cells. A file cell whose node cannot be resolved keeps
    its raw `latch://<id>.node` path.

    Records are yielded lazily, so the whole table is never held in memory at once, and enrichment
    is batched per page (one linked-name query and one node-path query per page). To cap a preview
    without enumerating the whole table, wrap the call with
    `itertools.islice(fetch_table_records(table_id), n)`.

    Args:
        table_id: The ID of the table to stream records from.
        page_size: The number of records fetched and enriched together per page.

    Yields:
        Each table record as a fully-preloaded `Record`.

    Raises:
        ValidationError: If a linked-record-names query response cannot be validated.
        RuntimeError: If a file/dir node-path query fails while resolving readable paths.
    """
    for page in Table(id=table_id).list_records(page_size=page_size):
        records = list(page.values())
        _preload_linked_record_names(records)
        _preload_file_paths(records)
        yield from records
