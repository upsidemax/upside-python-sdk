"""Every documented endpoint is reachable, and nothing undocumented is sent.

The three sets below are transcribed from the endpoint tables in the Upside API
reference (2026-09-10). Each call below drives one SDK method through the fake
session and records the ``action.type`` / query ``type`` / subscription ``type``
it actually put on the wire; the test then asserts the recorded set equals the
documented set. A new endpoint in the docs fails here until the SDK covers it,
and a typo in a wire type fails here too.
"""

import pytest

from upside import Exchange, Info
from upside.websocket_manager import WebsocketManager, subscription_to_identifier

# --- POST /exchange (section 4-5 of the reference) ---------------------------
EXCHANGE_ACTIONS = {
    "registerAccount",
    "enrollUserToMarketDeployer",
    "order",
    "cancel",
    "cancelByCloid",
    "cancelAll",
    "modify",
    "updateIsolatedMargin",
    "updateLeverage",
    "updateMarginMode",
    "updateSlippageSetting",
    "tpSl",
    "cancelTpSl",
    "cancelConditional",
    "lockCollateral",
    "unlockCollateral",
    "transferBetweenDeployers",
    "setMarginShareType",
    "transferMdToShareGroup",
    "transferShareGroupToMd",
    "lockIntoShareGroup",
    "unlockFromShareGroup",
    "approveAgent",
    "revokeAgent",
}

# --- POST /info (section 6) --------------------------------------------------
INFO_QUERIES = {
    "configs",
    "userMarketDeployers",
    "userAccount",
    "userOrders",
    "ordersByIds",
    "ordersByCloids",
    "candleSnapshot",
    "l2Book",
    "marketState",
    "shareGroupState",
    "userAgents",
    "accountByAddress",
    "ticker",
    "userFills",
    "orderHistory",
    "userFundingFlows",
}

# --- WebSocket channels (section 7) ------------------------------------------
WS_CHANNELS = {
    "l2Book",
    "candle",
    "bbo",
    "trades",
    "orderUpdates",
    "openOrders",
    "userFills",
    "config",
    "userAccount",
    "ticker",
    "allMarkets",
}

PRIVATE_KEY = "0x" + "01" * 32
AGENT_ADDRESS = "0x" + "cd" * 20

# One call per documented action. Trigger orders reuse the ``order`` action, so
# they ride along with the plain order rather than adding an entry.
EXCHANGE_CALLS = [
    lambda ex: ex.register_account(invite_code="A3K9Q2"),
    lambda ex: ex.enroll_user_to_market_deployer(2),
    lambda ex: ex.order(asset=1, is_buy=True, size="10", price="50"),
    lambda ex: ex.market_order(asset=1, is_buy=True, size="10", price="55"),
    lambda ex: ex.trigger_order(asset=1, is_buy=False, size="1", price="49", trigger_px="50", tpsl="sl"),
    lambda ex: ex.cancel(asset=1, oid=3),
    lambda ex: ex.cancel_by_cloid(asset=1, cloid=123),
    lambda ex: ex.cancel_all(asset=1),
    lambda ex: ex.modify(asset=1, oid=12345, price="150"),
    lambda ex: ex.update_isolated_margin(asset=1, ntli=5000),
    lambda ex: ex.update_leverage(asset=1, leverage=20),
    lambda ex: ex.update_margin_mode(asset=1, is_cross=False),
    lambda ex: ex.update_slippage_setting(market_deployer_id=1, market_slippage_bps=500),
    lambda ex: ex.tp_sl(asset=1, tp_price="90000", tp_limit_price="90000", tp_order_type=1),
    lambda ex: ex.cancel_tp_sl(asset=1),
    lambda ex: ex.cancel_conditional(oid=734796988633055604),
    lambda ex: ex.lock_collateral(1, 1, "1000"),
    lambda ex: ex.unlock_collateral(1, 1, "1000"),
    lambda ex: ex.transfer_between_deployers(1, 2, 1, "1000"),
    lambda ex: ex.set_margin_share_type(1),
    lambda ex: ex.transfer_md_to_share_group(1, 3, 1, "1000"),
    lambda ex: ex.transfer_share_group_to_md(3, 1, 1, "1000"),
    lambda ex: ex.lock_into_share_group(3, 1, "1000"),
    lambda ex: ex.unlock_from_share_group(3, 1, "1000"),
    lambda ex: ex.approve_agent(agent_address=AGENT_ADDRESS, agent_name="bot1"),
    lambda ex: ex.revoke_agent(AGENT_ADDRESS),
]

INFO_CALLS = [
    lambda info: info.configs(),
    lambda info: info.user_market_deployers(5),
    lambda info: info.user_account(5, 1),
    lambda info: info.user_orders(5, 1),
    lambda info: info.orders_by_ids(1, [8280]),
    lambda info: info.orders_by_cloids(5, 1, [1778844423064]),
    lambda info: info.candle_snapshot(1, "1m"),
    lambda info: info.l2_book(1),
    lambda info: info.market_state(1),
    lambda info: info.share_group_state(),
    lambda info: info.user_agents(5),
    lambda info: info.account_by_address("0xabc0000000000000000000000000000000000001"),
    lambda info: info.ticker(1),
    lambda info: info.user_fills(5),
    lambda info: info.order_history(5),
    lambda info: info.user_funding_flows(5),
]

WS_SUBSCRIPTIONS = [
    {"type": "l2Book", "asset": "1"},
    {"type": "candle", "asset": "1", "interval": "1m"},
    {"type": "bbo", "asset": "1"},
    {"type": "trades", "asset": "1"},
    {"type": "orderUpdates", "user": "0xabc"},
    {"type": "openOrders", "user": "0xabc"},
    {"type": "userFills", "user": "0xabc"},
    {"type": "config"},
    {"type": "userAccount", "user": "0xabc", "marketDeployerId": 1},
    {"type": "ticker", "asset": "1"},
    {"type": "allMarkets"},
]


@pytest.fixture
def exchange(fake_session):
    ex = Exchange(PRIVATE_KEY, base_url="https://dev.upsidemax.xyz")
    ex.session = fake_session
    return ex


@pytest.fixture
def info(fake_session):
    client = Info(base_url="https://dev.upsidemax.xyz", skip_ws=True)
    client.session = fake_session
    return client


def test_every_documented_exchange_action_is_sent(exchange, fake_session):
    for call in EXCHANGE_CALLS:
        call(exchange)
    sent = {r["json"]["action"]["type"] for r in fake_session.requests}
    assert sent == EXCHANGE_ACTIONS
    # Every request is a complete, signed envelope on the /exchange path.
    for request in fake_session.requests:
        assert request["url"].endswith("/exchange")
        assert set(request["json"]) >= {"action", "signature", "nonce"}
        assert request["json"]["signature"]["v"] in (27, 28)


def test_every_documented_info_query_is_sent(info, fake_session):
    for call in INFO_CALLS:
        call(info)
    sent = {r["json"]["type"] for r in fake_session.requests}
    assert sent == INFO_QUERIES
    for request in fake_session.requests:
        assert request["url"].endswith("/info")


def test_every_documented_ws_channel_can_be_subscribed():
    manager = WebsocketManager("https://dev.upsidemax.xyz")
    sent = []
    manager._send = sent.append  # type: ignore[method-assign]
    manager.ws_ready = True

    identifiers = set()
    for subscription in WS_SUBSCRIPTIONS:
        manager.subscribe(subscription, lambda _m: None)
        identifiers.add(subscription_to_identifier(subscription))

    assert {s["subscription"]["type"] for s in sent} == WS_CHANNELS
    assert all(s["method"] == "subscribe" for s in sent)
    # Distinct channels must not collide on one dispatch key.
    assert len(identifiers) == len(WS_SUBSCRIPTIONS)
