"""Base HTTP transport shared by :class:`~upside.info.Info` and
:class:`~upside.exchange.Exchange`.

Everything is a JSON ``POST`` to ``/info`` or ``/exchange`` over one persistent
session. Gateway-level failures (non-2xx, or a body with ``"status": "error"``)
raise :class:`~upside.utils.error.ClientError` / ``ServerError``; business-level
outcomes (per-order ``statuses[].error``, non-zero ``errorCode``) are returned
verbatim for the caller to inspect.
"""

import logging
from json import JSONDecodeError
from typing import Any, Optional, cast

import requests

from .utils import constants
from .utils.error import ClientError, ServerError
from .utils.types import Json


class API:
    """Thin POST-only client with status-based exception mapping."""

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[float] = None) -> None:
        self.base_url = (base_url or constants.UAT_API_URL).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._logger = logging.getLogger("upside")

    def post(self, url_path: str, payload: Optional[Any] = None) -> Json:
        """POST ``payload`` to ``url_path`` and return the parsed JSON body."""
        url = self.base_url + url_path
        response = self.session.post(url, json=payload or {}, timeout=self.timeout)
        return self._handle_response(response)

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
