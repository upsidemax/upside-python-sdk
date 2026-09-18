"""Exception hierarchy for the Upside SDK.

Upside reports failures at two levels:

* **HTTP / gateway level** — a non-2xx response (or a body with
  ``"status": "error"``) carrying a machine ``code`` and ``message``. These
  raise :class:`ClientError` (4xx) or :class:`ServerError` (5xx).
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
    """A 5xx error, or a gateway timeout (504) / downstream failure (503)."""


class WebsocketError(UpsideError):
    """Raised for WebSocket subscription/protocol problems."""
