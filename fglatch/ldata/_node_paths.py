from collections.abc import Iterable
from enum import StrEnum

import gql
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
