"""Authoritative Latch workspace resolution: refuse to silently default."""

import logging
import os

from latch_sdk_config.user import user_config

logger = logging.getLogger(__name__)


def resolve_workspace(*, workspace: str) -> str:
    """
    Assert `workspace` against ambient config and return it.

    Reads the ambient workspace from `$LATCH_WORKSPACE`, then the persisted user
    config. It never falls through to the account-default query, so a caller who has
    not configured a workspace does not publish somewhere chosen for them.

    Args:
        workspace: The workspace id the caller intends to act on.

    Returns:
        The passed workspace id.

    Raises:
        ValueError: If an ambient workspace is set and differs from `workspace`.
    """
    ambient = os.environ.get("LATCH_WORKSPACE") or user_config.workspace_id or None

    if ambient is not None and ambient != workspace:
        raise ValueError(
            f"ambient workspace {ambient!r} conflicts with requested {workspace!r}; "
            f"unset LATCH_WORKSPACE or pass the matching id"
        )
    if ambient is None:
        logger.warning("no ambient workspace configured; proceeding with %s", workspace)

    return workspace
