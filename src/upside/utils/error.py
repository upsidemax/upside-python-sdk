"""Exception hierarchy for the Upside SDK.

Upside reports failures at three levels:

* **HTTP / gateway level** — a non-2xx response (or a body with
  ``"status": "error"``) carrying a machine ``code`` and ``message``. These
  raise :class:`ClientError` (4xx) or :class:`ServerError` (5xx).
* **Transport level** — no HTTP response at all. ``/info`` reads are retried;
  if every attempt fails the ``requests`` exception propagates. An
  ``/exchange`` write that may have been delivered raises
  :class:`OutcomeUnknownError` instead of being resent.
* **Business level** — an HTTP 200 with ``"status": "ok"`` that still contains
  a per-item ``error`` string inside ``statuses[]`` or a non-zero ``errorCode``
  inside ``response.data``. These are **not** raised — inspect the returned
  dict. See https://docs.upsidemax.xyz/guide/error-codes.
"""

from typing import Any, Dict, List, Optional


class UpsideError(Exception):
    """Base class for every error raised by the SDK."""


class APIError(UpsideError):
    """An HTTP/gateway-level rejection from ``/info`` or ``/exchange``.

    Attributes mirror the error envelope
    ``{"status": "error", "requestId", "code", "message"}``. A parameter
    rejection (``code == "INVALID_PARAM"``) also carries ``errors``: one
    ``{"field", "reason", "expected"}`` entry per offending field.
    """

    def __init__(
        self,
        status_code: int,
        code: Optional[str] = None,
        message: Optional[str] = None,
        request_id: Optional[str] = None,
        errors: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id
        self.errors = errors or []
        detail = "".join(
            f" [{e.get('field')}: {e.get('reason')}"
            + (f", expected {e.get('expected')}]" if e.get("expected") else "]")
            for e in self.errors
            if isinstance(e, dict)
        )
        super().__init__(f"HTTP {status_code} {code or ''}: {message or ''}{detail}".rstrip())


class ClientError(APIError):
    """A 4xx rejection (bad request, signature invalid, nonce reused, ...)."""


class ServerError(APIError):
    """A 5xx error, or a gateway timeout (504) / downstream failure (503).

    On ``/exchange`` a ``504 DOWNSTREAM_TIMEOUT`` does **not** mean the action
    failed — only that the gateway gave up waiting for its result. Treat it like
    :class:`OutcomeUnknownError`.
    """


class OutcomeUnknownError(UpsideError):
    """An ``/exchange`` request got no response after it may have been delivered.

    Raised on a read timeout or a connection dropped mid-response. The action
    may or may not have executed, and the SDK does not resend it: the identical
    envelope reuses the ``nonce`` and is rejected (``DUPLICATE_NONCE``) whether
    or not the first attempt landed. Confirm the outcome (``orderUpdates`` /
    ``userOrders`` / ``userFills``, or look the order up by ``cloid``) and sign
    a fresh request if it did not execute. The underlying ``requests``
    exception is ``__cause__``.

    Attributes:
        action_type: ``action["type"]`` of the request, when known.
        nonce: the nonce the request was signed with, when known.
    """

    def __init__(self, message: str, action_type: Optional[str] = None, nonce: Optional[int] = None) -> None:
        super().__init__(message)
        self.action_type = action_type
        self.nonce = nonce


class WebsocketError(UpsideError):
    """Raised for WebSocket subscription/protocol problems."""
