"""Exchange action tests: envelope construction, wire shapes, error mapping.

Uses a fake session so nothing hits the network; signatures are real and are
verified by recovering the signer address from the captured envelope.
"""

import pytest
from eth_account import Account

from upside import Cloid, Exchange
from upside.utils.error import ClientError, ServerError

PRIVATE_KEY = "0x" + "01" * 32
ADDRESS = "0x1a642f0e3c3af545e7acbd38b07251b3990914f1"


@pytest.fixture
def exchange(fake_session):
    ex = Exchange(PRIVATE_KEY, base_url="https://dev.upsidemax.xyz")
    ex.session = fake_session
    return ex


def _action(fake_session):
    return fake_session.last["json"]["action"]


def test_wallet_address_resolved(exchange):
    assert exchange.address == ADDRESS


def test_order_builds_limit_wire(exchange, fake_session):
    exchange.order(asset=1, is_buy=True, size="10", price="50", cloid=Cloid.from_int(42))
    env = fake_session.last["json"]
    assert env["nonce"] > 0
    assert set(env) == {"action", "signature", "nonce"}
    action = env["action"]
    assert action["type"] == "order"
    assert action["grouping"] == "na"
    assert action["orders"] == [
        {"a": 1, "b": True, "s": "10", "r": False, "p": "50", "t": {"limit": {"tif": "Gtc"}}, "c": "42"}
    ]


def test_market_order_omits_price(exchange, fake_session):
    exchange.market_order(asset=2, is_buy=False, size="5")
    order = _action(fake_session)["orders"][0]
    assert order == {"a": 2, "b": False, "s": "5", "r": False, "t": {"market": {}}}
    assert "p" not in order


def test_order_url_and_path(exchange, fake_session):
    exchange.order(asset=1, is_buy=True, size="1", price="1")
    assert fake_session.last["url"] == "https://dev.upsidemax.xyz/exchange"


def test_signature_recovers_to_wallet(exchange, fake_session):
    from upside.utils import signing

    exchange.order(asset=1, is_buy=True, size="10", price="50")
    env = fake_session.last["json"]
    digest = signing.eip712_digest(env["action"], env["nonce"])
    sig = env["signature"]
    recovered = Account._recover_hash(digest, vrs=(sig["v"], int(sig["r"], 16), int(sig["s"], 16)))
    assert recovered.lower() == ADDRESS


def test_bulk_orders_rejects_oversized_batch(exchange):
    orders = [{"asset": 1, "is_buy": True, "size": "1", "price": "1"} for _ in range(11)]
    with pytest.raises(ValueError):
        exchange.bulk_orders(orders)  # type: ignore[arg-type]


def test_order_requires_price_when_not_market(exchange):
    with pytest.raises(ValueError):
        exchange.order(asset=1, is_buy=True, size="10")


def test_cancel_and_cancel_by_cloid_wire(exchange, fake_session):
    exchange.cancel(asset=1, oid=3)
    assert _action(fake_session) == {"type": "cancel", "cancels": [{"a": 1, "o": 3}]}
    exchange.cancel_by_cloid(asset=1, cloid=123)
    assert _action(fake_session) == {"type": "cancelByCloid", "cancels": [{"a": 1, "cloid": "123"}]}


def test_cancel_all_wire(exchange, fake_session):
    exchange.cancel_all(asset=7)
    assert _action(fake_session) == {"type": "cancelAll", "a": 7}


def test_modify_only_includes_changed_fields(exchange, fake_session):
    exchange.modify(asset=1, oid=12345, price="150", size="8", tif="Gtc")
    assert _action(fake_session) == {"type": "modify", "a": 1, "oid": 12345, "p": "150", "s": "8", "tif": "Gtc"}


def test_modify_requires_locator(exchange):
    with pytest.raises(ValueError):
        exchange.modify(asset=1, price="1")


def test_update_margin_mode_omits_hedge_when_none(exchange, fake_session):
    exchange.update_margin_mode(asset=1, is_cross=True)
    assert _action(fake_session) == {"type": "updateMarginMode", "asset": 1, "isCross": True}
    exchange.update_margin_mode(asset=1, is_cross=False, is_hedge=True)
    assert _action(fake_session)["isHedge"] is True


def test_tp_sl_wire_defaults(exchange, fake_session):
    exchange.tp_sl(asset=1, tp_price="90000", sl_price="80000")
    action = _action(fake_session)
    assert action["type"] == "tpSl"
    assert action["a"] == 1
    assert action["tpPrice"] == "90000" and action["slPrice"] == "80000"
    assert action["isPositionTpsl"] is True


def test_register_account_puts_invite_code_at_top_level(exchange, fake_session):
    exchange.register_account(invite_code="A3K9Q2")
    env = fake_session.last["json"]
    assert env["inviteCode"] == "A3K9Q2"
    assert "inviteCode" not in env["action"]  # not signed
    assert env["action"] == {"type": "registerAccount", "address": ADDRESS}


def test_register_account_captures_account_id(exchange, fake_session):
    fake_session.queue({"status": "ok", "response": {"type": "registerAccount", "accountId": "3"}})
    exchange.register_account(invite_code="X")
    assert exchange.account_id == "3"


def test_approve_agent_generates_key(exchange, fake_session):
    resp, agent_key = exchange.approve_agent()
    action = _action(fake_session)
    assert action["type"] == "approveAgent"
    assert action["agentName"] == "" and action["validUntil"] == 0
    assert agent_key is not None and Account.from_key(agent_key).address.lower() == action["agentAddress"]


def test_approve_agent_with_address_returns_no_key(exchange, fake_session):
    addr = "0x" + "cd" * 20
    _, agent_key = exchange.approve_agent(agent_address=addr, agent_name="bot1", valid_until=123)
    assert agent_key is None
    assert _action(fake_session)["agentAddress"] == addr


def test_client_error_on_status_error(exchange, fake_session):
    fake_session.queue({"status": "error", "code": "NONCE_REUSED", "message": "dup", "requestId": "req-1"}, 409)
    with pytest.raises(ClientError) as exc:
        exchange.cancel(asset=1, oid=1)
    assert exc.value.code == "NONCE_REUSED"
    assert exc.value.status_code == 409
    assert exc.value.request_id == "req-1"


def test_server_error_on_5xx(exchange, fake_session):
    fake_session.queue({"status": "error", "code": "INTERNAL_ERROR", "message": "boom"}, 500)
    with pytest.raises(ServerError):
        exchange.cancel(asset=1, oid=1)


def test_business_error_is_not_raised(exchange, fake_session):
    # HTTP 200 + status ok with a per-item error must be returned, not raised.
    fake_session.queue({"status": "ok", "response": {"type": "cancel", "data": {"statuses": [{"error": "x"}]}}})
    result = exchange.cancel(asset=1, oid=1)
    assert result["response"]["data"]["statuses"][0]["error"] == "x"


def test_register_account_without_invite_has_no_invite_key(exchange, fake_session):
    exchange.register_account()
    env = fake_session.last["json"]
    assert "inviteCode" not in env


def test_enroll_user_to_market_deployer(exchange, fake_session):
    exchange.enroll_user_to_market_deployer(2)
    assert _action(fake_session) == {"type": "enrollUserToMarketDeployer", "marketDeployerId": 2}


def test_revoke_agent(exchange, fake_session):
    addr = "0x" + "cd" * 20
    exchange.revoke_agent(addr)
    assert _action(fake_session) == {"type": "revokeAgent", "agentAddress": addr}


def test_bulk_orders_multiple(exchange, fake_session):
    orders = [
        {"asset": 1, "is_buy": True, "size": "1", "price": "10"},
        {"asset": 1, "is_buy": False, "size": "2", "is_market": True},
    ]
    exchange.bulk_orders(orders)  # type: ignore[arg-type]
    action = _action(fake_session)
    assert len(action["orders"]) == 2
    assert action["orders"][1]["t"] == {"market": {}}


def test_bulk_orders_builder_fields(exchange, fake_session):
    exchange.bulk_orders(
        [{"asset": 1, "is_buy": True, "size": "1", "price": "10", "builder_address": "0xab", "builder_fee": 3}]  # type: ignore[list-item]
    )
    order = _action(fake_session)["orders"][0]
    assert order["builderAddress"] == "0xab" and order["builderFee"] == 3


def test_bulk_cancel_multiple(exchange, fake_session):
    exchange.bulk_cancel([{"asset": 1, "oid": 3}, {"asset": 2, "oid": 4}])
    assert _action(fake_session)["cancels"] == [{"a": 1, "o": 3}, {"a": 2, "o": 4}]


def test_bulk_cancel_by_cloid_multiple(exchange, fake_session):
    exchange.bulk_cancel_by_cloid([{"asset": 1, "cloid": "100"}, {"asset": 1, "cloid": 200}])  # type: ignore[list-item]
    assert _action(fake_session)["cancels"] == [{"a": 1, "cloid": "100"}, {"a": 1, "cloid": "200"}]


def test_bulk_cancel_rejects_empty(exchange):
    with pytest.raises(ValueError):
        exchange.bulk_cancel([])


def test_modify_by_cloid_updates_new_cloid(exchange, fake_session):
    exchange.modify(asset=1, cloid=100, price="151", new_cloid=200)
    assert _action(fake_session) == {"type": "modify", "a": 1, "cloid": "100", "p": "151", "c": "200"}


def test_update_isolated_margin(exchange, fake_session):
    exchange.update_isolated_margin(asset=1, ntli=5000, is_buy=True)
    assert _action(fake_session) == {"type": "updateIsolatedMargin", "asset": 1, "ntli": 5000, "isBuy": True}
    exchange.update_isolated_margin(asset=1, ntli=-100)
    assert "isBuy" not in _action(fake_session)


def test_update_fee_setting_omits_cleared_legs(exchange, fake_session):
    exchange.update_fee_setting(1, taker_bps=5, maker_bps=-2)
    assert _action(fake_session) == {"type": "updateFeeSetting", "marketDeployerId": 1, "takerBps": 5, "makerBps": -2}
    exchange.update_fee_setting(1)
    assert _action(fake_session) == {"type": "updateFeeSetting", "marketDeployerId": 1}


def test_cancel_conditional_and_cancel_tp_sl(exchange, fake_session):
    exchange.cancel_conditional(oid=99)
    assert _action(fake_session) == {"type": "cancelConditional", "oid": 99}
    exchange.cancel_tp_sl(asset=1, position_side=2)
    assert _action(fake_session) == {"type": "cancelTpSl", "a": 1, "positionSide": 2}


def test_tp_sl_standalone_includes_order_side(exchange, fake_session):
    exchange.tp_sl(asset=1, sl_price="80000", is_position_tpsl=False, order_side="S")
    assert _action(fake_session)["orderSide"] == "S"


@pytest.mark.parametrize(
    "call,expected",
    [
        (
            lambda ex: ex.lock_collateral(1, 1, "1000"),
            {"type": "lockCollateral", "marketDeployerId": 1, "coinId": 1, "amount": "1000"},
        ),
        (
            lambda ex: ex.unlock_collateral(1, 1, 500),
            {"type": "unlockCollateral", "marketDeployerId": 1, "coinId": 1, "amount": "500"},
        ),
        (
            lambda ex: ex.transfer_between_deployers(1, 2, 1, "1000"),
            {
                "type": "transferBetweenDeployers",
                "fromMarketDeployerId": 1,
                "toMarketDeployerId": 2,
                "coinId": 1,
                "amount": "1000",
            },
        ),
    ],
)
def test_typed_collateral_actions_wire(exchange, fake_session, call, expected):
    call(exchange)
    assert _action(fake_session) == expected


def test_typed_path_signature_recovers(exchange, fake_session):
    # Collateral actions use the Typed EIP-712 path; the signature must still
    # recover to the wallet address.
    from upside.utils import signing

    exchange.lock_collateral(1, 1, "1000")
    env = fake_session.last["json"]
    digest = signing.eip712_digest(env["action"], env["nonce"])
    sig = env["signature"]
    recovered = Account._recover_hash(digest, vrs=(sig["v"], int(sig["r"], 16), int(sig["s"], 16)))
    assert recovered.lower() == ADDRESS


def test_custom_nonce_manager_is_used(fake_session):
    from upside.utils.signing import NonceManager

    class Fixed(NonceManager):
        def next(self) -> int:
            return 42

    ex = Exchange(PRIVATE_KEY, base_url="https://dev.upsidemax.xyz", nonce_manager=Fixed())
    ex.session = fake_session
    ex.cancel_all(1)
    assert fake_session.last["json"]["nonce"] == 42
