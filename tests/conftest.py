"""Shared test fixtures: an in-memory fake ``requests.Session``."""

import json
from typing import Any, Dict, List, Optional

import pytest


class FakeResponse:
    def __init__(self, body: Any, status_code: int = 200):
        self._body = body
        self.status_code = status_code
        self.text = json.dumps(body) if not isinstance(body, str) else body

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self) -> Any:
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


class FakeSession:
    """Records POSTs and replays a queued (or default) response."""

    def __init__(self) -> None:
        self.headers: Dict[str, str] = {}
        self.requests: List[Dict[str, Any]] = []
        self._responses: List[FakeResponse] = []
        self.default = FakeResponse({"status": "ok", "response": {"data": {}}})

    def queue(self, body: Any, status_code: int = 200) -> None:
        self._responses.append(FakeResponse(body, status_code))

    def post(self, url: str, json: Optional[Any] = None, timeout: Optional[float] = None) -> FakeResponse:  # noqa: A002
        self.requests.append({"url": url, "json": json, "timeout": timeout})
        return self._responses.pop(0) if self._responses else self.default

    @property
    def last(self) -> Dict[str, Any]:
        return self.requests[-1]

    def close(self) -> None:  # pragma: no cover
        pass


@pytest.fixture
def fake_session():
    return FakeSession()
