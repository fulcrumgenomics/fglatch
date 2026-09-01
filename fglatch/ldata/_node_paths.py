from collections.abc import Iterable
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

import gql
from latch.registry.record import Record
from latch.types.directory import LatchDir
from latch.types.file import LatchFile
from latch.types.utils import is_absolute_node_path
from latch.types.utils import old_style_path
from latch_sdk_gql import JsonValue
from latch_sdk_gql.execute import execute


class _OldStylePathKey(StrEnum):
    """The named match groups of `latch.types.utils.old_style_path`."""

    ACCOUNT_ROOT = "account_root"
    MOUNT = "mount"
    MOUNT_GCP = "mount_gcp"
    MOUNT_AZURE = "mount_azure"


# The mount variants share their group name with their output domain suffix.
_MOUNT_KEYS = (_OldStylePathKey.MOUNT, _OldStylePathKey.MOUNT_GCP, _OldStylePathKey.MOUNT_AZURE)


def _format_node_path(raw: str | None, owner: str | None) -> str | None:
    """
    Format an `(ldataGetPath, ldataOwner)` pair into a readable path, as `format_path` does.

    Returns None when the pair cannot be formatted, so callers omit the id (and fall back to the
    raw node path). Mirrors the branch logic in `latch.types.utils.format_path` — including that the
    unanchored `old_style_path` regex lets `mount` shadow the `mount_gcp`/`mount_azure` forms.
    """
    if raw is None:
        return None

    match = old_style_path.match(raw)
    if match is None:
        return None

    parts = raw.split("/")
    key = "/".join(parts[2:])

    for mount_key in _MOUNT_KEYS:
        if match[mount_key] is not None:
            return f"latch://{parts[1]}.{mount_key}/{key}"
    if match[_OldStylePathKey.ACCOUNT_ROOT] is not None and owner is not None:
        return f"latch://{owner}.account/{key}"

    return None


def resolve_node_paths(node_ids: Iterable[str], *, chunk_size: int = 1000) -> dict[str, str]:
    """
    Resolve `latch://<id>.node` node ids to readable paths, batched.

    Deduplicates the ids, then issues one aliased GraphQL query per `chunk_size` ids (each id
    contributes an aliased `ldataGetPath` + `ldataOwner`), and applies the same local formatting as
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
    node_ids: dict[str, None] = {}  # ordered set: dedupe while preserving first-seen order
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


def _prime_file_paths(records: Iterable[Record], *, chunk_size: int = 1000) -> None:
    """Resolve every file/dir node path in `records`' values and rewrite the cells in place."""
    records = list(records)
    node_ids = _collect_file_node_ids(records)
    if not node_ids:
        return

    node_paths = resolve_node_paths(node_ids, chunk_size=chunk_size)
    for record in records:
        values = record.get_values(load_if_missing=False)
        if values is None:
            continue
        for key, value in values.items():
            if isinstance(value, list):
                values[key] = [_rewrite_node_path(item, node_paths) for item in value]
            else:
                values[key] = _rewrite_node_path(value, node_paths)
