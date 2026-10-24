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
from typing import Any, Callable, Dict, List, NamedTuple, Optional, cast

import websocket

from .utils.constants import INTERVALS
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


def normalize_subscription(subscription: Subscription) -> Subscription:
    """The wire form of ``subscription``: string ``asset``, lowercase ``user``, int ``group``."""
    normalized: Dict[str, Any] = dict(subscription)
    if "asset" in normalized and not isinstance(normalized["asset"], bool):
        normalized["asset"] = str(normalized["asset"])
    if isinstance(normalized.get("user"), str):
        normalized["user"] = normalized["user"].lower()
    if normalized.get("group") is not None:
        normalized["group"] = int(normalized["group"])
    if normalized.get("type") == "candle" and normalized.get("interval") not in INTERVALS:
        raise WebsocketError(
            f"candle interval must be one of {', '.join(INTERVALS)}, got {normalized.get('interval')!r}"
        )
    return cast(Subscription, normalized)


def _field(subscription: Subscription, name: str) -> Any:
    """Read a subscription field the channel requires, or say which one is missing."""
    try:
        return subscription[name]  # type: ignore[literal-required]
    except KeyError:
        raise WebsocketError(f'subscription type {subscription["type"]!r} requires {name!r}') from None


def _l2book_identifier(asset: Any, group: Any) -> str:
    """``l2Book:<asset>`` for the raw book, ``l2Book:<asset>,<group>`` per grouping."""
    group = int(group) if group else 0
    return f"l2Book:{asset},{group}" if group else f"l2Book:{asset}"


def subscription_to_identifier(subscription: Subscription) -> str:
    """Stable key for a subscription, matched against inbound messages."""
    sub_type = subscription["type"]
    if sub_type == "l2Book":
        # Each price grouping is its own channel; no group (or 0) is the raw book.
        return _l2book_identifier(_field(subscription, "asset"), subscription.get("group"))
    if sub_type in ("bbo", "trades", "ticker"):
        return f'{sub_type}:{_field(subscription, "asset")}'
    if sub_type == "candle":
        return f'candle:{_field(subscription, "asset")},{_field(subscription, "interval")}'
    if sub_type in ("orderUpdates", "openOrders", "userFills"):
        return f'{sub_type}:{str(_field(subscription, "user")).lower()}'
    if sub_type == "userAccount":
        # The account view differs per market deployer, so both keys matter.
        return f'userAccount:{str(_field(subscription, "user")).lower()},{_field(subscription, "marketDeployerId")}'
    if sub_type in ("config", "allMarkets"):
        return sub_type
    raise WebsocketError(f"unknown subscription type: {sub_type}")


def ws_message_to_identifier(message: Dict[str, Any]) -> Optional[str]:
    """Derive the identifier for an inbound push, or ``None`` for control frames."""
    channel = message.get("channel")
    if channel is None or channel in _CONTROL_CHANNELS:
        return None

    data: Any = message.get("data")
    if channel == "l2Book":
        return _l2book_identifier(data.get("asset"), data.get("group"))
    if channel == "bbo":
        return f'bbo:{data.get("asset")}'
    if channel == "trades":
        # An empty subscription snapshot names no asset and cannot be routed;
        # it carries nothing either.
        first = data[0] if isinstance(data, list) and data else {}
        return f'trades:{first.get("asset")}' if first else None
    if channel == "candle":
        # Every frame is one candle under the compact keys. The batch form
        # (long keys) is from an older server that sent a subscription snapshot.
        if isinstance(data, dict) and "candles" in data:
            return f'candle:{data.get("asset")},{data.get("interval")}'
        return f'candle:{data.get("s")},{data.get("i")}'
    if channel in ("config", "allMarkets"):
        return str(channel)

    # Everything else arrives as "<base>.<suffix>" (e.g. "fills.0xabc",
    # "ticker.1", "userAccount.0xabc.1").
    base, _, suffix = channel.partition(".")
    sub_type = _USER_CHANNEL_TO_TYPE.get(base)
    if sub_type is not None:
        return f"{sub_type}:{suffix.lower()}"
    if base == "ticker":
        return f"ticker:{suffix}"
    if base == "l2Book" and isinstance(data, dict):
        # GroupRemoved names the grouped channel ("l2Book.<asset>.<group>").
        return _l2book_identifier(data.get("asset"), data.get("group"))
    if base == "userAccount":
        addr, _, deployer = suffix.partition(".")
        if not deployer and isinstance(data, dict):
            # Some frames name only the address; the deployer is in the payload.
            deployer = str(data.get("marketDeployerId", ""))
        # With neither, this ends in "," and matches no exact key -- dispatch
        # treats that as "every deployer subscribed for this address".
        return f"userAccount:{addr.lower()},{deployer}"
    return None


class _ActiveSubscription(NamedTuple):
    callback: WsCallback
    subscription_id: int
    subscription: Subscription


class WebsocketManager(threading.Thread):
    """Background WebSocket connection with subscribe/dispatch/reconnect.

    Callbacks run on the connection's reader thread: keep them short (a callback
    blocking past the pong timeout makes the client reconnect).

    The server answers a refused subscription with an ``error`` frame that does
    not say which subscription it refers to, so it cannot reach a subscription
    callback; ``error_callback`` receives those frames, and ``AuthResult``
    frames with ``success: false``. Subscription fields are normalized before
    sending (``asset`` as a decimal string, ``user`` lowercase, ``group`` as an
    int) because the server refuses a numeric ``asset``.
    """

    def __init__(
        self,
        base_url: str,
        ping_interval: int = 30,
        user_agent: str = "upside-python-sdk",
        error_callback: Optional[WsCallback] = None,
    ) -> None:
        super().__init__(daemon=True)
        if ping_interval <= 10:
            raise ValueError("ping_interval must be > 10 seconds (the pong timeout is ping_interval - 5)")
        self.error_callback = error_callback
        # http(s)://host  ->  ws(s)://host/ws
        self.ws_url = "ws" + base_url.rstrip("/")[len("http") :] + "/ws"
        self.ping_interval = ping_interval
        # The API edge refuses WebSocket upgrades that carry no User-Agent header.
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
        """Register ``callback`` for ``subscription`` and return a subscription id.

        Safe to call while disconnected: the subscription is sent on (re)connect.
        """
        subscription = normalize_subscription(subscription)
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
        subscription = normalize_subscription(subscription)
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

        if message.get("channel") == "error" or (message.get("msg") == "AuthResult" and not message.get("success")):
            self._logger.warning("ws error: %s", message)
            if self.error_callback is not None:
                try:
                    self.error_callback(message)
                except Exception:  # pragma: no cover - user callback error
                    self._logger.exception("error in ws error callback")
            return

        try:
            identifier = ws_message_to_identifier(message)
        except Exception:
            # A frame we cannot route is dropped here: letting the exception
            # escape would reach _on_error as if the connection had failed.
            self._logger.warning("dropping unroutable ws frame: %s", raw[:120], exc_info=True)
            return
        if identifier is None:
            return
        with self._lock:
            callbacks = self._callbacks_for(identifier)
            if message.get("msg") == "GroupRemoved":
                # The server has already dropped every subscription to this
                # grouping; forget it so a reconnect does not replay a
                # subscription that would now be refused.
                self._active.pop(identifier, None)
        if not callbacks:
            self._logger.debug("no subscriber for ws frame %s", identifier)
        for callback in callbacks:
            try:
                callback(message)
            except Exception:  # pragma: no cover - user callback error
                self._logger.exception("error in ws callback for %s", identifier)

    def _on_error(self, _ws: Any, error: Any) -> None:
        # With auto-reconnect, websocket-client reports a dropped connection
        # here without calling on_close; stop sending until on_open fires again.
        # It also reports an exception raised by any of our callbacks here, on
        # a connection that is still up — that must not stop sends, or every
        # later subscribe would wait for a reconnect that never comes.
        self._logger.warning("websocket error: %s", error)
        if isinstance(error, (websocket.WebSocketException, OSError)):
            with self._lock:
                self.ws_ready = False

    def _on_close(self, _ws: Any, status_code: Any, msg: Any) -> None:
        self._logger.debug("websocket closed: %s %s", status_code, msg)
        with self._lock:
            self.ws_ready = False

    # -- internals --------------------------------------------------------
    def _callbacks_for(self, identifier: str) -> List[WsCallback]:
        """Callbacks registered for ``identifier``. Call with the lock held.

        A ``userAccount`` frame that names neither a market deployer in its
        channel nor one in its payload yields a key ending in ``","``; rather
        than drop it, hand it to every deployer subscribed for that address.
        """
        entries = self._active.get(identifier)
        if entries is None and identifier.endswith(","):
            return [e.callback for key, v in self._active.items() if key.startswith(identifier) for e in v]
        return [e.callback for e in entries or []]

    def _send(self, payload: Dict[str, Any]) -> None:
        """Send now, or leave it to the replay in ``_on_open`` if the socket is gone."""
        try:
            self.ws.send(json.dumps(payload))
        except websocket.WebSocketConnectionClosedException:
            self.ws_ready = False
            self._logger.debug("socket closed; %s will be replayed on reconnect", payload.get("method", "frame"))
