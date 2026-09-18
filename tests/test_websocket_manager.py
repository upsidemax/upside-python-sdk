"""WebSocket identifier mapping and subscription-registry tests (no real socket)."""

import pytest

from upside.utils.error import WebsocketError
from upside.websocket_manager import (
    WebsocketManager,
    subscription_to_identifier,
    ws_message_to_identifier,
)


@pytest.mark.parametrize(
    "subscription,expected",
    [
        ({"type": "l2Book", "asset": "1"}, "l2Book:1"),
        ({"type": "bbo", "asset": "23"}, "bbo:23"),
        ({"type": "trades", "asset": "1"}, "trades:1"),
        ({"type": "candle", "asset": "1", "interval": "1m"}, "candle:1,1m"),
        ({"type": "config"}, "config"),
        ({"type": "orderUpdates", "user": "0xABC"}, "orderUpdates:0xabc"),
        ({"type": "openOrders", "user": "0xAbC"}, "openOrders:0xabc"),
        ({"type": "userFills", "user": "0xABC"}, "userFills:0xabc"),
    ],
)
def test_subscription_to_identifier(subscription, expected):
    assert subscription_to_identifier(subscription) == expected


def test_subscription_to_identifier_unknown():
    with pytest.raises(WebsocketError):
        subscription_to_identifier({"type": "mystery"})


@pytest.mark.parametrize(
    "message,expected",
    [
        ({"channel": "l2Book", "data": {"asset": "1"}}, "l2Book:1"),
        ({"channel": "bbo", "data": {"asset": "23"}}, "bbo:23"),
        ({"channel": "trades", "data": [{"asset": "1", "px": "1"}]}, "trades:1"),
        ({"channel": "candle", "data": {"s": "1", "i": "1m"}}, "candle:1,1m"),
        ({"channel": "config", "data": []}, "config"),
        ({"msg": "OrderUpdate", "channel": "orderUpdates.0xABC", "data": []}, "orderUpdates:0xabc"),
        ({"msg": "OpenOrdersSnapshot", "channel": "openOrders.0xABC", "data": []}, "openOrders:0xabc"),
        ({"msg": "TradeFill", "channel": "fills.0xABC", "data": []}, "userFills:0xabc"),
        ({"channel": "subscriptionResponse", "data": {}}, None),
        ({"channel": "error", "data": {"code": "BAD_SUBSCRIPTION"}}, None),
    ],
)
def test_ws_message_to_identifier(message, expected):
    assert ws_message_to_identifier(message) == expected


def test_subscription_roundtrip_matches_for_user_channel():
    sub = {"type": "userFills", "user": "0xAbC123"}
    msg = {"msg": "TradeFill", "channel": "fills.0xabc123", "data": []}
    assert subscription_to_identifier(sub) == ws_message_to_identifier(msg)


def test_subscribe_dispatch_without_socket():
    # Exercise the registry + dispatch path with a stubbed _send (no network).
    manager = WebsocketManager("https://dev.upsidemax.xyz")
    sent = []
    manager._send = lambda payload: sent.append(payload)  # type: ignore[method-assign]
    manager.ws_ready = True

    received = []
    sub = {"type": "l2Book", "asset": "1"}
    sub_id = manager.subscribe(sub, lambda m: received.append(m))
    assert sent[-1] == {"method": "subscribe", "subscription": {"type": "l2Book", "asset": "1"}}

    manager._on_message(None, '{"channel": "l2Book", "data": {"asset": "1", "bookVersion": 5}}')
    assert received and received[0]["data"]["bookVersion"] == 5

    # Second callback for the same identifier does NOT re-send subscribe.
    before = len(sent)
    manager.subscribe(sub, lambda m: received.append(m))
    assert len(sent) == before

    # Unsubscribing the last callback sends unsubscribe.
    manager.unsubscribe(sub, sub_id)
    manager.unsubscribe(sub, sub_id + 1)
    assert sent[-1] == {"method": "unsubscribe", "subscription": {"type": "l2Book", "asset": "1"}}


def test_ws_url_derivation():
    assert WebsocketManager("https://dev.upsidemax.xyz").ws_url == "wss://dev.upsidemax.xyz/ws"
    assert WebsocketManager("http://localhost:8080").ws_url == "ws://localhost:8080/ws"


def _stub(manager):
    sent = []
    manager._send = lambda payload: sent.append(payload)  # type: ignore[method-assign]
    return sent


def test_subscribe_before_ready_defers_then_on_open_replays():
    manager = WebsocketManager("https://dev.upsidemax.xyz")
    sent = _stub(manager)
    manager.subscribe({"type": "l2Book", "asset": "1"}, lambda m: None)
    assert sent == []  # not ready → nothing sent yet
    manager._on_open(None)
    assert sent == [{"method": "subscribe", "subscription": {"type": "l2Book", "asset": "1"}}]
    assert manager.ws_ready is True


def test_on_open_sends_auth_when_configured():
    manager = WebsocketManager("https://dev.upsidemax.xyz")
    sent = _stub(manager)
    manager.authenticate(3)  # not ready → stored, not sent
    assert sent == []
    manager._on_open(None)
    assert {"msg": "Auth", "accountId": 3} in sent


def test_authenticate_when_ready_sends_immediately():
    manager = WebsocketManager("https://dev.upsidemax.xyz")
    sent = _stub(manager)
    manager.ws_ready = True
    manager.authenticate(7)
    assert sent == [{"msg": "Auth", "accountId": 7}]


def test_on_message_ignores_control_and_bad_frames():
    manager = WebsocketManager("https://dev.upsidemax.xyz")
    _stub(manager)
    received = []
    manager.ws_ready = True
    manager.subscribe({"type": "config"}, lambda m: received.append(m))
    manager._on_message(None, "not json")  # must not raise
    manager._on_message(None, '{"channel": "subscriptionResponse", "data": {}}')  # control → no dispatch
    manager._on_message(None, '{"channel": "error", "data": {"code": "BAD_SUBSCRIPTION"}}')  # logged, no dispatch
    assert received == []
    manager._on_message(None, '{"msg": "ConfigChanged", "channel": "config", "data": [{"entityId": "1"}]}')
    assert received and received[0]["data"][0]["entityId"] == "1"


def test_unsubscribe_missing_returns_false():
    manager = WebsocketManager("https://dev.upsidemax.xyz")
    _stub(manager)
    manager.ws_ready = True
    assert manager.unsubscribe({"type": "config"}, 999) is False


def test_on_close_marks_not_ready():
    manager = WebsocketManager("https://dev.upsidemax.xyz")
    manager.ws_ready = True
    manager._on_close(None, 1000, "bye")
    assert manager.ws_ready is False


def test_callback_exception_is_isolated():
    manager = WebsocketManager("https://dev.upsidemax.xyz")
    _stub(manager)
    manager.ws_ready = True

    def boom(_m):
        raise RuntimeError("callback failure")

    manager.subscribe({"type": "config"}, boom)
    # Must swallow the callback error rather than propagate.
    manager._on_message(None, '{"msg": "ConfigChanged", "channel": "config", "data": []}')


@pytest.mark.parametrize(
    "subscription,expected",
    [
        ({"type": "ticker", "asset": "10000001"}, "ticker:10000001"),
        ({"type": "allMarkets"}, "allMarkets"),
        ({"type": "userAccount", "user": "0xABC", "marketDeployerId": 1}, "userAccount:0xabc,1"),
    ],
)
def test_subscription_to_identifier_new_channels(subscription, expected):
    assert subscription_to_identifier(subscription) == expected


@pytest.mark.parametrize(
    "message,expected",
    [
        ({"msg": "Ticker", "channel": "ticker.10000001", "data": {"asset": "10000001"}}, "ticker:10000001"),
        ({"msg": "AllMarkets", "channel": "allMarkets", "data": {"markets": []}}, "allMarkets"),
        (
            {"msg": "UserAccount", "channel": "userAccount.0xABC.1", "data": {}},
            "userAccount:0xabc,1",
        ),
        (
            # Some frames name only the address; the deployer is in the payload.
            {"msg": "UserAccount", "channel": "userAccount.0xABC", "data": {"marketDeployerId": 1}},
            "userAccount:0xabc,1",
        ),
    ],
)
def test_ws_message_to_identifier_new_channels(message, expected):
    assert ws_message_to_identifier(message) == expected


def test_candle_subscription_snapshot_frame_routes_like_the_increments():
    """The first candle frame is a batch under long keys, not one bar under s/i."""
    snapshot = {
        "channel": "candle",
        "isSnapshot": True,
        "data": {"asset": "1", "interval": "1m", "candles": [{"t": 1, "closed": True}]},
    }
    increment = {"channel": "candle", "data": {"s": "1", "i": "1m", "closed": False}}
    assert ws_message_to_identifier(snapshot) == ws_message_to_identifier(increment) == "candle:1,1m"
    assert subscription_to_identifier({"type": "candle", "asset": "1", "interval": "1m"}) == "candle:1,1m"


def test_user_account_subscription_is_per_deployer():
    manager = WebsocketManager("https://dev.upsidemax.xyz")
    sent = _stub(manager)
    manager.ws_ready = True

    first, second = [], []
    manager.subscribe({"type": "userAccount", "user": "0xabc", "marketDeployerId": 1}, first.append)
    manager.subscribe({"type": "userAccount", "user": "0xabc", "marketDeployerId": 2}, second.append)
    assert len(sent) == 2  # same address, different deployer -> two subscriptions

    manager._on_message(None, '{"msg":"UserAccount","channel":"userAccount.0xabc.2","data":{"crossEquity":"7"}}')
    assert second and second[0]["data"]["crossEquity"] == "7"
    assert first == []


def test_ticker_and_all_markets_dispatch():
    manager = WebsocketManager("https://dev.upsidemax.xyz")
    _stub(manager)
    manager.ws_ready = True

    ticks, markets = [], []
    manager.subscribe({"type": "ticker", "asset": "1"}, ticks.append)
    manager.subscribe({"type": "allMarkets"}, markets.append)

    manager._on_message(None, '{"msg":"Ticker","channel":"ticker.1","data":{"asset":"1","lastPx":"10"}}')
    manager._on_message(None, '{"msg":"AllMarkets","channel":"allMarkets","data":{"ts":1,"markets":[]}}')
    assert ticks and ticks[0]["data"]["lastPx"] == "10"
    assert markets and markets[0]["data"]["markets"] == []


def test_snapshot_frames_reach_private_channel_subscribers():
    """orderUpdates / userFills open with a history snapshot under a different msg."""
    manager = WebsocketManager("https://dev.upsidemax.xyz")
    _stub(manager)
    manager.ws_ready = True

    orders, fills = [], []
    manager.subscribe({"type": "orderUpdates", "user": "0xabc"}, orders.append)
    manager.subscribe({"type": "userFills", "user": "0xabc"}, fills.append)

    manager._on_message(
        None,
        '{"msg":"OrderHistorySnapshot","channel":"orderUpdates.0xabc",'
        '"data":{"rows":[{"order_id":"1"}],"count":1,"truncated":false}}',
    )
    manager._on_message(
        None,
        '{"msg":"TradeFillHistorySnapshot","channel":"fills.0xabc",'
        '"data":{"rows":[{"exec_id":"9"}],"count":1,"truncated":false}}',
    )
    assert orders and orders[0]["data"]["rows"][0]["order_id"] == "1"
    assert fills and fills[0]["data"]["rows"][0]["exec_id"] == "9"


@pytest.mark.parametrize(
    "subscription,missing",
    [
        ({"type": "ticker"}, "asset"),
        ({"type": "candle", "asset": "1"}, "interval"),
        ({"type": "userAccount", "user": "0xabc"}, "marketDeployerId"),
        ({"type": "orderUpdates"}, "user"),
    ],
)
def test_missing_subscription_field_raises_websocket_error(subscription, missing):
    """A missing required field is a subscription problem, not a stray KeyError."""
    with pytest.raises(WebsocketError, match=missing):
        subscription_to_identifier(subscription)


def test_user_account_frame_without_a_deployer_still_dispatches():
    """Neither channel nor payload names the deployer: fan out, don't drop."""
    manager = WebsocketManager("https://dev.upsidemax.xyz")
    _stub(manager)
    manager.ws_ready = True

    first, second, other = [], [], []
    manager.subscribe({"type": "userAccount", "user": "0xabc", "marketDeployerId": 1}, first.append)
    manager.subscribe({"type": "userAccount", "user": "0xabc", "marketDeployerId": 2}, second.append)
    manager.subscribe({"type": "userAccount", "user": "0xdef", "marketDeployerId": 1}, other.append)

    manager._on_message(None, '{"msg":"UserAccount","channel":"userAccount.0xabc","data":{"crossEquity":"7"}}')
    assert len(first) == 1 and len(second) == 1
    assert other == []  # a different address is still untouched
