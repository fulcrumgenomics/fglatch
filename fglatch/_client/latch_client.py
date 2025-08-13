"""A class for making rate-limited requests to the Latch API."""

from typing import Literal
from typing import cast

from latch.utils import current_workspace
from latch.utils import retrieve_or_login
from latch_sdk_config.latch import config
from requests import Session
from requests_ratelimiter import Duration
from requests_ratelimiter import Limiter
from requests_ratelimiter import LimiterSession
from requests_ratelimiter import RequestRate

from fglatch._client.models import Execution
from fglatch._client.models import ListedExecutions
from fglatch.type_aliases import ExecutionIdAsString
from fglatch.type_aliases._type_aliases import LatchWorkspaceId

LATCH_API_RATE: RequestRate = RequestRate(limit=10, interval=Duration.SECOND * 1)
"""
The self-imposed rate limit for Latch API requests.

Latch does not (currently) have a rate limit on requests to its API, but we strive to be good
neighbors, and we would like to avoid being the reason a rate limit is introduced. 10 requests per
seconds seems like a rate we should not anticipate exceeding.
"""


class LatchClient:
    """Rate-limited requests to the Latch API."""

    _session: Session
    _workspace_id: LatchWorkspaceId
    _auth_header: dict[Literal["Authorization"], str]

    def __init__(
        self,
        token: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        """
        Initialize the client.

        Requests made by the client are rate-limited to 10 requests per second. (Latch does not
        enforce an API rate limit; this is a self-imposed safeguard.)

        Args:
            token: A Latch user API token. If not provided, the current user's token will be
                retrieved from `~/.latch/token`. If there is no currently authenticated user, a
                login prompt will open in the browser. After login, the authenticated user's token
                will be retrieved from `~/.latch/token`.
            workspace_id: A Latch workspace ID. If not provided, the active workspace will be
                retrieved from `~/.latch/workspace`. If there is no currently active workspace, the
                default workspace ID will be retrieved from the user's account.
        """
        if token is None:
            token = retrieve_or_login()

        if workspace_id is None:
            self._workspace_id = current_workspace()
        else:
            self._workspace_id = workspace_id

        self._auth_header = {"Authorization": f"Bearer {token}"}
        self._session = LimiterSession(limiter=Limiter(LATCH_API_RATE))

    def get_executions(self) -> dict[ExecutionIdAsString, Execution]:
        """
        Retrieve execution metadata from Latch's `get-executions` endpoint.

        The body of this function is adapted from
        `latch_cli.services.get_executions.get_executions()`, and adds pydantic model validation.

        Returns:
            Metadata for all executions in the specified workspace. The results are returned as a
            mapping of workspace IDs (as strings) to blobs of execution metadata.

        Raises:
            HTTPError: If the POST request to the `get-executions` endpoint failed.
            ValidationError: If the POST request's response was malformatted.
        """
        # The `cast()` is required because Mypy does not currently infer that a string literal is a
        # string when the literal is a key in a mapping.
        # https://github.com/python/mypy/issues/18494
        headers: dict[str, str] = cast(dict[str, str], self._auth_header)

        resp = self._session.post(
            url=config.api.execution.list,
            headers=headers,
            json={"ws_account_id": self._workspace_id},
        )

        resp.raise_for_status()

        executions = ListedExecutions.model_validate(resp.json())

        return executions.root
