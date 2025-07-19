"""A class for making rate-limited requests to the Latch API."""

from typing import Literal

from latch.utils import current_workspace
from latch.utils import retrieve_or_login
from requests import Session
from requests_ratelimiter import Duration
from requests_ratelimiter import Limiter
from requests_ratelimiter import LimiterSession
from requests_ratelimiter import RequestRate

from fglatch.lib.type_aliases import LatchUserApiToken
from fglatch.lib.type_aliases import LatchWorkspaceId

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
        token: LatchUserApiToken | None = None,
        workspace_id: LatchWorkspaceId | None = None,
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

        Raises:
            ValueError: If the tenant prefix contains non-alphanumeric characters or if the API key
                environment variable is not set.
        """
        if token is None:
            token = retrieve_or_login()

        if workspace_id is None:
            self._workspace_id = current_workspace()
        else:
            self._workspace_id = workspace_id

        self._auth_header = {"Authorization": f"Bearer {token}"}
        self._session = LimiterSession(limiter=Limiter(LATCH_API_RATE))
