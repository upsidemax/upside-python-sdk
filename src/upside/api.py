"""Base HTTP transport shared by :class:`~upside.info.Info` and
:class:`~upside.exchange.Exchange`.

Everything is a JSON ``POST`` to ``/info`` or ``/exchange`` over one persistent
session. Gateway-level failures (non-2xx, or a body with ``"status": "error"``)
raise :class:`~upside.utils.error.ClientError` / ``ServerError``; business-level
outcomes (per-order ``statuses[].error``, non-zero ``errorCode``) are returned
verbatim for the caller to inspect.

Timeouts and retries
--------------------
Every request has a timeout (``DEFAULT_TIMEOUT`` unless you pass one). What
happens when the network fails depends on whether resending is safe:

* ``/info`` is read-only, so a read timeout, a dropped connection, or a
  transient ``502`` / ``503`` / ``504`` is retried up to ``max_retries`` times
  with exponential backoff. ``503 NOT_READY`` (the contract has no book yet) is
  a data state, not a transient fault, and is raised immediately.
* ``/exchange`` is retried **only when the request never left this machine**
  (connection refused, DNS failure, connect timeout) — resending the identical
  signed envelope is then harmless. Once the request may have reached the
  gateway, a resend carries the same ``nonce`` and is rejected
  (``DUPLICATE_NONCE``) even though the first attempt may have executed, so a
  read timeout or a connection dropped mid-response raises
  :class:`~upside.utils.error.OutcomeUnknownError` instead. Confirm the outcome
  from ``orderUpdates`` / ``userOrders`` / ``userFills`` before re-signing.
"""

import logging
import random
import time
from json import JSONDecodeError
from typing import Any, Callable, Optional, Tuple, Union, cast

import requests
from urllib3.exceptions import NewConnectionError

from .utils import constants
from .utils.error import ClientError, OutcomeUnknownError, ServerError
from .utils.types import Json

# ``requests`` timeout: one number for both phases, or ``(connect, read)``.
Timeout = Union[float, Tuple[float, float]]

# Connect / read timeout in seconds. The read budget covers a ``sync: true``
# order, which the gateway answers within 2 s (or with 504).
DEFAULT_TIMEOUT: Tuple[float, float] = (5.0, 10.0)

# Retries after the first attempt, and the first backoff in seconds (doubled per
# retry, with jitter, capped at ``_MAX_BACKOFF``).
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF = 0.5
_MAX_BACKOFF = 8.0

# HTTP statuses on /info that a retry can cure: the edge or a downstream service
# was briefly unavailable or slow.
_RETRYABLE_STATUS = frozenset({502, 503, 504})
# ...except these codes, which describe the data rather than a passing fault.
_NON_RETRYABLE_CODES = frozenset({"NOT_READY"})

# Transport failures on /info worth another attempt.
_RETRYABLE_TRANSPORT = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


class API:
    """Thin POST-only client with status-based exception mapping and retries.

    ``timeout`` is seconds, or ``(connect, read)``; ``None`` means
    :data:`DEFAULT_TIMEOUT`. ``max_retries=0`` disables retrying.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[Timeout] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff: float = DEFAULT_BACKOFF,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.base_url = (base_url or constants.DEVNET_API_URL).rstrip("/")
        self.timeout: Timeout = DEFAULT_TIMEOUT if timeout is None else timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._logger = logging.getLogger("upside")
        self._sleep: Callable[[float], None] = time.sleep

    def post(self, url_path: str, payload: Optional[Any] = None) -> Json:
        """POST ``payload`` to ``url_path`` and return the parsed JSON body.

        Only ``/info`` is treated as safe to resend; see the module docstring.
        """
        url = self.base_url + url_path
        idempotent = url_path == "/info"
        attempt = 0
        while True:
            try:
                response = self.session.post(url, json=payload or {}, timeout=self.timeout)
            except requests.exceptions.RequestException as exc:
                not_sent = _never_sent(exc)
                if attempt < self.max_retries and (not_sent or (idempotent and isinstance(exc, _RETRYABLE_TRANSPORT))):
                    attempt = self._wait_before_retry(attempt, url_path, exc)
                    continue
                if not idempotent and not not_sent:
                    raise OutcomeUnknownError(
                        f"POST {url_path}: no response ({type(exc).__name__}); the request may have executed",
                    ) from exc
                raise
            if idempotent and attempt < self.max_retries and _is_transient(response):
                attempt = self._wait_before_retry(attempt, url_path, f"HTTP {response.status_code}")
                continue
            return self._handle_response(response)

    def _wait_before_retry(self, attempt: int, url_path: str, reason: object) -> int:
        delay = min(self.backoff * (2**attempt), _MAX_BACKOFF) * random.uniform(0.5, 1.0)
        self._logger.warning(
            "POST %s failed (%s); retry %d/%d in %.2fs", url_path, reason, attempt + 1, self.max_retries, delay
        )
        self._sleep(delay)
        return attempt + 1

    def _handle_response(self, response: requests.Response) -> Json:
        try:
            body = response.json()
        except (JSONDecodeError, ValueError):
            body = None

        if response.ok and not (isinstance(body, dict) and body.get("status") == "error"):
            if body is None:
                raise ServerError(response.status_code, message=f"non-JSON response: {response.text[:200]}")
            return cast(Json, body)

        code = message = request_id = None
        errors = None
        if isinstance(body, dict):
            code = body.get("code")
            message = body.get("message")
            request_id = body.get("requestId")
            raw_errors = body.get("errors")
            # Field-level detail, sent with INVALID_PARAM rejections. Keep only
            # the object entries: a bare string here must not cost the caller
            # the whole error envelope.
            if isinstance(raw_errors, list):
                errors = [e for e in raw_errors if isinstance(e, dict)]
        else:
            message = response.text[:500] or None

        error_cls = ClientError if 400 <= response.status_code < 500 else ServerError
        raise error_cls(response.status_code, code=code, message=message, request_id=request_id, errors=errors)

    def close(self) -> None:
        self.session.close()


def _never_sent(exc: requests.exceptions.RequestException) -> bool:
    """Whether the request provably never reached the server.

    True for a connect timeout and for a connection that could not be opened
    (refused, unreachable, DNS failure). Anything later — a read timeout, a
    reset mid-response — may have been processed.
    """
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        cause = exc.args[0] if exc.args else None
        return isinstance(getattr(cause, "reason", None), NewConnectionError)
    return False


def _is_transient(response: requests.Response) -> bool:
    if response.status_code not in _RETRYABLE_STATUS:
        return False
    try:
        body = response.json()
    except (JSONDecodeError, ValueError):
        return True  # an edge error page, not a gateway verdict
    return not (isinstance(body, dict) and body.get("code") in _NON_RETRYABLE_CODES)
