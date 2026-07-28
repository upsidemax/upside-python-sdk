"""Threaded WebSocket client for the Upside realtime API.

Runs ``websocket-client``'s ``run_forever`` on a background thread with built-in
ping and auto-reconnect. Subscriptions use the v0.14 protocol
``{"method": "subscribe", "subscription": {...}}``; each is keyed by a stable
*identifier* so incoming pushes can be routed to the right callback(s), and so
active subscriptions can be replayed after a reconnect.

See https://docs.upsidemax.xyz/websocket/overview.
"""

import json
import logging
import threading
from collections import defaultdict
from typing import Any, Callable, Dict, List, NamedTuple, Optional

import websocket

from .utils.error import WebsocketError
from .utils.types import Json, Subscription

WsCallback = Callable[[Json], None]

# Control channels that carry no subscription payload.
_CONTROL_CHANNELS = {"subscriptionResponse", "error", "pong"}

# Inbound channel prefix (before the ".<addr>") -> subscription type.
_USER_CHANNEL_TO_TYPE = {
    "orderUpdates": "orderUpdates",
    "openOrders": "openOrders",
    "fills": "userFills",
}


def subscription_to_identifier(subscription: Subscription) -> str:
    """Stable key for a subscription, matched against inbound messages."""
    sub_type = subscription["type"]
    if sub_type in ("l2Book", "bbo", "trades"):
        return f'{sub_type}:{subscription["asset"]}'
    if sub_type == "candle":
        return f'candle:{subscription["asset"]},{subscription["interval"]}'
    if sub_type in ("orderUpdates", "openOrders", "userFills"):
        return f'{sub_type}:{str(subscription["user"]).lower()}'
    if sub_type == "config":
        return "config"
    raise WebsocketError(f"unknown subscription type: {sub_type}")


def ws_message_to_identifier(message: Dict[str, Any]) -> Optional[str]:
    """Derive the identifier for an inbound push, or ``None`` for control frames."""
    channel = message.get("channel")
    if channel is None or channel in _CONTROL_CHANNELS:
        return None

    data: Any = message.get("data")
    if channel in ("l2Book", "bbo"):
        return f'{channel}:{data.get("asset")}'
    if channel == "trades":
        first = data[0] if isinstance(data, list) and data else {}
        return f'trades:{first.get("asset")}'
    if channel == "candle":
        return f'candle:{data.get("s")},{data.get("i")}'
    if channel == "config":
        return "config"

    # User channels arrive as "<base>.<address>" (e.g. "fills.0xabc").
    base, _, addr = channel.partition(".")
    sub_type = _USER_CHANNEL_TO_TYPE.get(base)
    if sub_type is not None:
        return f"{sub_type}:{addr.lower()}"
    return None


class _ActiveSubscription(NamedTuple):
    callback: WsCallback
    subscription_id: int
    subscription: Subscription


class WebsocketManager(threading.Thread):
    """Background WebSocket connection with subscribe/dispatch/reconnect."""

    def __init__(self, base_url: str, ping_interval: int = 30, user_agent: str = "upside-python-sdk") -> None:
        super().__init__(daemon=True)
        # http(s)://host  ->  ws(s)://host/ws
        self.ws_url = "ws" + base_url.rstrip("/")[len("http") :] + "/ws"
        self.ping_interval = ping_interval
        # A User-Agent header is required to pass the CloudFront edge in front of QA.
        self._headers = [f"User-Agent: {user_agent}"]
        self._logger = logging.getLogger("upside.ws")

        self._lock = threading.Lock()
        self._id_counter = 0
        self.ws_ready = False
        self._auth_account_id: Optional[int] = None
        self._active: Dict[str, List[_ActiveSubscription]] = defaultdict(list)

        self.ws = websocket.WebSocketApp(
            self.ws_url,
            header=self._headers,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

    # -- thread entrypoint ------------------------------------------------
    def run(self) -> None:
        self.ws.run_forever(ping_interval=self.ping_interval, ping_timeout=self.ping_interval - 5, reconnect=5)

    def stop(self) -> None:
        try:
            self.ws.close()
        except Exception:  # pragma: no cover - best-effort teardown
            pass

    # -- public API -------------------------------------------------------
    def authenticate(self, account_id: int) -> None:
        """Send an ``Auth`` frame (optional today; recommended for forward compat)."""
        self._auth_account_id = account_id
        if self.ws_ready:
            self._send({"msg": "Auth", "accountId": account_id})

    def subscribe(self, subscription: Subscription, callback: WsCallback) -> int:
        """Register ``callback`` for ``subscription`` and return a subscription id."""
        identifier = subscription_to_identifier(subscription)
        with self._lock:
            self._id_counter += 1
            sub_id = self._id_counter
            first_for_identifier = not self._active[identifier]
            self._active[identifier].append(_ActiveSubscription(callback, sub_id, subscription))
            # If the socket isn't ready yet, _on_open replays every active
            # subscription; only send now for the first callback of an identifier.
            if self.ws_ready and first_for_identifier:
                self._send({"method": "subscribe", "subscription": dict(subscription)})
        return sub_id

    def unsubscribe(self, subscription: Subscription, subscription_id: int) -> bool:
        """Remove one callback; send ``unsubscribe`` when the last one is gone."""
        identifier = subscription_to_identifier(subscription)
        with self._lock:
            entries = self._active.get(identifier, [])
            remaining = [e for e in entries if e.subscription_id != subscription_id]
            removed = len(remaining) != len(entries)
            if remaining:
                self._active[identifier] = remaining
            else:
                self._active.pop(identifier, None)
                if removed and self.ws_ready:
                    self._send({"method": "unsubscribe", "subscription": dict(subscription)})
        return removed

    # -- socket callbacks -------------------------------------------------
    def _on_open(self, _ws: Any) -> None:
        self._logger.debug("websocket open: %s", self.ws_url)
        with self._lock:
            self.ws_ready = True
            if self._auth_account_id is not None:
                self._send({"msg": "Auth", "accountId": self._auth_account_id})
            # Replay every active subscription (covers both first connect and reconnect).
            for entries in self._active.values():
                if entries:
                    self._send({"method": "subscribe", "subscription": dict(entries[0].subscription)})

    def _on_message(self, _ws: Any, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            self._logger.warning("dropping non-JSON ws frame: %s", raw[:120])
            return
        if not isinstance(message, dict):
            return

        if message.get("channel") == "error":
            self._logger.warning("ws subscription error: %s", message.get("data"))

        identifier = ws_message_to_identifier(message)
        if identifier is None:
            return
        with self._lock:
            callbacks = [e.callback for e in self._active.get(identifier, [])]
        for callback in callbacks:
            try:
                callback(message)
            except Exception:  # pragma: no cover - user callback error
                self._logger.exception("error in ws callback for %s", identifier)

    def _on_error(self, _ws: Any, error: Any) -> None:
        self._logger.warning("websocket error: %s", error)

    def _on_close(self, _ws: Any, status_code: Any, msg: Any) -> None:
        self._logger.debug("websocket closed: %s %s", status_code, msg)
        with self._lock:
            self.ws_ready = False

    # -- internals --------------------------------------------------------
    def _send(self, payload: Dict[str, Any]) -> None:
        self.ws.send(json.dumps(payload))
