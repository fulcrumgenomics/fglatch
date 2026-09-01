import pytest
from latch.types.utils import format_path
from latch.types.utils import old_style_path
from pytest_mock import MockerFixture

from fglatch.ldata._node_paths import _format_node_path
from fglatch.ldata._node_paths import _OldStylePathKey
from fglatch.ldata._node_paths import resolve_node_paths

# Each SDK `ldataGetPath` shape, its `ldataOwner`, and the readable path `format_path` produces.
# `mount_gcp`/`mount_azure` resolve to `.mount`: the SDK's unanchored `old_style_path` regex lets
# the `mount` alternative shadow them. These cases pin that (quirky) parity with `format_path`.
PATH_SHAPES: list[tuple[str, str | None, str]] = [
    ("mount/mybucket/a/b.txt", None, "latch://mybucket.mount/a/b.txt"),
    ("mount_gcp/mybucket/a/b.txt", None, "latch://mybucket.mount/a/b.txt"),
    ("mount_azure/mybucket/a/b.txt", None, "latch://mybucket.mount/a/b.txt"),
    ("account_root/123/a/b.txt", "123", "latch://123.account/a/b.txt"),
]


@pytest.mark.parametrize(
    ("raw", "owner", "expected"), PATH_SHAPES, ids=[s[0].split("/")[0] for s in PATH_SHAPES]
)
def test_format_node_path_matches_each_shape(raw: str, owner: str | None, expected: str) -> None:
    """`_format_node_path` reproduces `format_path`'s local formatting for each path shape."""
    assert _format_node_path(raw, owner) == expected


@pytest.mark.parametrize(
    ("raw", "owner", "expected"), PATH_SHAPES, ids=[s[0].split("/")[0] for s in PATH_SHAPES]
)
def test_resolve_node_paths_parity_with_format_path(
    mocker: MockerFixture, raw: str, owner: str | None, expected: str
) -> None:
    """`resolve_node_paths([id])[id]` equals `format_path(f"latch://{id}.node")` per shape."""
    mocker.patch("fglatch.ldata._node_paths.execute", return_value={"p0": raw, "o0": owner})
    mocker.patch(
        "latch.types.utils.execute", return_value={"ldataGetPath": raw, "ldataOwner": owner}
    )

    assert resolve_node_paths(["5"])["5"] == format_path("latch://5.node") == expected


def test_resolve_node_paths_deduplicates(mocker: MockerFixture) -> None:
    """Repeated ids collapse to one aliased lookup and one map entry."""
    execute_mock = mocker.patch(
        "fglatch.ldata._node_paths.execute",
        return_value={"p0": "mount/b/k", "o0": None, "p1": "mount/b/k2", "o1": None},
    )

    result = resolve_node_paths(["1", "1", "2"])

    execute_mock.assert_called_once()
    assert set(execute_mock.call_args.kwargs["variables"]) == {"id0", "id1"}
    assert set(result) == {"1", "2"}


def test_resolve_node_paths_chunks(mocker: MockerFixture) -> None:
    """N ids over chunk_size issue ceil(N / chunk_size) queries, partitioned in order."""
    execute_mock = mocker.patch(
        "fglatch.ldata._node_paths.execute",
        side_effect=[
            {"p0": "mount/b/k1", "o0": None, "p1": "mount/b/k2", "o1": None},
            {"p0": "mount/b/k3", "o0": None},
        ],
    )

    result = resolve_node_paths(["1", "2", "3"], chunk_size=2)

    assert execute_mock.call_count == 2
    assert execute_mock.call_args_list[0].kwargs["variables"] == {"id0": "1", "id1": "2"}
    assert execute_mock.call_args_list[1].kwargs["variables"] == {"id0": "3"}
    assert result == {
        "1": "latch://b.mount/k1",
        "2": "latch://b.mount/k2",
        "3": "latch://b.mount/k3",
    }


def test_resolve_node_paths_omits_unresolvable(mocker: MockerFixture) -> None:
    """A node whose path cannot be resolved is omitted from the result."""
    mocker.patch(
        "fglatch.ldata._node_paths.execute",
        return_value={"p0": None, "o0": None, "p1": "mount/b/k", "o1": None},
    )

    result = resolve_node_paths(["missing", "ok"])

    assert "missing" not in result
    assert result["ok"] == "latch://b.mount/k"


def test_resolve_node_paths_empty_input_makes_no_query(mocker: MockerFixture) -> None:
    """An empty id list returns `{}` without issuing a query."""
    execute_mock = mocker.patch("fglatch.ldata._node_paths.execute")

    assert resolve_node_paths([]) == {}
    execute_mock.assert_not_called()


@pytest.mark.parametrize(
    ("raw", "owner"),
    [
        pytest.param(None, None, id="none-path"),
        pytest.param("not_an_old_style_path", None, id="unrecognized-prefix"),
        pytest.param("account_root/123/a/b.txt", None, id="account-root-without-owner"),
    ],
)
def test_format_node_path_returns_none_for_unresolvable(raw: str | None, owner: str | None) -> None:
    """`_format_node_path` returns None (caller omits/falls back) when it cannot format."""
    assert _format_node_path(raw, owner) is None


def test_old_style_path_keys_cover_all_regex_groups() -> None:
    """`_OldStylePathKey` must enumerate every `old_style_path` group, or parity drifts silently."""
    assert {key.value for key in _OldStylePathKey} == set(old_style_path.groupindex)


def test_resolve_node_paths_collects_and_raises_all_chunk_errors(mocker: MockerFixture) -> None:
    """A chunk failure does not stop the others; every failure is surfaced in one raised error."""
    mocker.patch(
        "fglatch.ldata._node_paths.execute",
        side_effect=[
            {"p0": "mount/b/k", "o0": None},
            RuntimeError("boom-2"),
            RuntimeError("boom-3"),
        ],
    )

    with pytest.raises(RuntimeError, match="2 chunk") as excinfo:
        resolve_node_paths(["1", "2", "3"], chunk_size=1)

    message = str(excinfo.value)
    assert "boom-2" in message
    assert "boom-3" in message
