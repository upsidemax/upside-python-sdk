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


def test_market_order_carries_price(exchange, fake_session):
    """A market order must send ``p``: the server derives no price of its own."""
    exchange.market_order(asset=2, is_buy=False, size="5", price="61000")
    order = _action(fake_session)["orders"][0]
    assert order == {"a": 2, "b": False, "s": "5", "r": False, "p": "61000", "t": {"market": {}}}


def test_order_without_price_is_rejected_locally(exchange, fake_session):
    """Catch the missing price before signing, not as a 400 from the server."""
    import pytest

    with pytest.raises(ValueError, match="price is required"):
        exchange.order(asset=2, is_buy=True, size="5", is_market=True)
    with pytest.raises(ValueError, match="price is required"):
        exchange.order(asset=2, is_buy=True, size="5")


def test_order_url_and_path(exchange, fake_session):
    exchange.order(asset=1, is_buy=True, size="1", price="1")
    assert fake_session.last["url"] == "https://dev.upsidemax.xyz/exchange"


def test_signature_recovers_to_wallet(exchange, fake_session):
    from upside.utils import signing

    exchange.order(asset=1, is_buy=True, size="10", price="50")
    env = fake_session.last["json"]
    digest = signing.eip712_digest(env["action"], env["nonce"], chain_id=9767)
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
    exchange.tp_sl(
        asset=1,
        tp_price="90000",
        tp_limit_price="90000",
        tp_order_type=1,
        sl_price="80000",
        sl_limit_price="80000",
        sl_order_type=2,
    )
    action = _action(fake_session)
    assert action["type"] == "tpSl"
    assert action["a"] == 1
    assert action["tpPrice"] == "90000" and action["slPrice"] == "80000"
    assert action["isPositionTpsl"] is True
    # Both legs carry the order type the server has required since 2026-09-11.
    assert action["tpOrderType"] == 1 and action["slOrderType"] == 2
    assert action["positionSide"] == 0 and action["reduceOnly"] is False
    assert action["tpSize"] == "0" and action["tpTriggerType"] == 0


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
        {"asset": 1, "is_buy": False, "size": "2", "price": "9", "is_market": True},
    ]
    exchange.bulk_orders(orders)  # type: ignore[arg-type]
    action = _action(fake_session)
    assert len(action["orders"]) == 2
    assert action["orders"][1]["t"] == {"market": {}}
    assert action["orders"][1]["p"] == "9"  # market orders carry a price too


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


def test_cancel_conditional_and_cancel_tp_sl(exchange, fake_session):
    exchange.cancel_conditional(oid=99)
    assert _action(fake_session) == {"type": "cancelConditional", "oid": 99}
    exchange.cancel_tp_sl(asset=1, position_side=2)
    assert _action(fake_session) == {"type": "cancelTpSl", "a": 1, "positionSide": 2}


def test_tp_sl_standalone_includes_order_side(exchange, fake_session):
    exchange.tp_sl(
        asset=1,
        sl_price="80000",
        sl_limit_price="79000",
        sl_order_type=2,
        is_position_tpsl=False,
        order_side="S",
    )
    action = _action(fake_session)
    assert action["orderSide"] == "S"
    assert action["isPositionTpsl"] is False
    # The unset leg still ships, zeroed.
    assert action["tpPrice"] == "0" and action["tpOrderType"] == 0


def test_tp_sl_requires_order_type_for_a_set_leg(exchange):
    with pytest.raises(ValueError, match="tp_order_type is required"):
        exchange.tp_sl(asset=1, tp_price="90000", tp_limit_price="90000")


def test_tp_sl_requires_limit_price_for_a_set_leg(exchange):
    """Market TP/SL stopped meaning "limit price 0" on 2026-09-11."""
    with pytest.raises(ValueError, match="sl_limit_price must be > 0"):
        exchange.tp_sl(asset=1, sl_price="80000", sl_order_type=2)


def test_tp_sl_rejects_order_type_without_trigger(exchange):
    with pytest.raises(ValueError, match="tp_order_type without tp_price"):
        exchange.tp_sl(asset=1, sl_price="1", sl_limit_price="1", sl_order_type=1, tp_order_type=1)


def test_tp_sl_requires_at_least_one_leg(exchange):
    with pytest.raises(ValueError, match="set tp_price or sl_price"):
        exchange.tp_sl(asset=1)


def test_update_leverage_wire(exchange, fake_session):
    exchange.update_leverage(asset=1, leverage=20)
    assert _action(fake_session) == {"type": "updateLeverage", "a": 1, "leverage": 20}


def test_update_slippage_setting_wire(exchange, fake_session):
    exchange.update_slippage_setting(market_deployer_id=1, market_slippage_bps=500)
    assert _action(fake_session) == {
        "type": "updateSlippageSetting",
        "marketDeployerId": 1,
        "marketSlippageBps": 500,
    }


@pytest.mark.parametrize("bps", [0, -1, 10001])
def test_update_slippage_setting_rejects_out_of_range(exchange, bps):
    with pytest.raises(ValueError, match=r"\(0, 10000\]"):
        exchange.update_slippage_setting(market_deployer_id=1, market_slippage_bps=bps)


def test_set_margin_share_type_wire(exchange, fake_session):
    exchange.set_margin_share_type(1)
    assert _action(fake_session) == {"type": "setMarginShareType", "marginShareType": 1}


@pytest.mark.parametrize(
    "call,expected",
    [
        (
            lambda ex: ex.transfer_md_to_share_group(1, 3, 1, "1000"),
            {
                "type": "transferMdToShareGroup",
                "marketDeployerId": 1,
                "groupId": 3,
                "coinId": 1,
                "amount": "1000",
            },
        ),
        (
            lambda ex: ex.transfer_share_group_to_md(3, 1, 1, 1000),
            {
                "type": "transferShareGroupToMd",
                "groupId": 3,
                "marketDeployerId": 1,
                "coinId": 1,
                "amount": "1000",
            },
        ),
        (
            lambda ex: ex.lock_into_share_group(3, 1, "1000"),
            {"type": "lockIntoShareGroup", "groupId": 3, "coinId": 1, "amount": "1000"},
        ),
        (
            lambda ex: ex.unlock_from_share_group(3, 1, "1000"),
            {"type": "unlockFromShareGroup", "groupId": 3, "coinId": 1, "amount": "1000"},
        ),
    ],
)
def test_share_group_transfer_wire(exchange, fake_session, call, expected):
    call(exchange)
    assert _action(fake_session) == expected


def test_trigger_order_wire(exchange, fake_session):
    exchange.trigger_order(
        asset=1, is_buy=False, size="10", price="79000", trigger_px="80000", tpsl="sl", reduce_only=True
    )
    order = _action(fake_session)["orders"][0]
    assert order == {
        "a": 1,
        "b": False,
        "s": "10",
        "r": True,
        "p": "79000",
        "t": {"trigger": {"triggerPx": "80000", "isMarket": True, "tpsl": "sl"}},
    }


def test_trigger_order_limit_variant(exchange, fake_session):
    exchange.trigger_order(asset=1, is_buy=True, size="1", price="100", trigger_px="99", tpsl="tp", is_market=False)
    assert _action(fake_session)["orders"][0]["t"]["trigger"]["isMarket"] is False


def test_trigger_order_requires_direction(exchange):
    from upside.exchange import _order_to_wire

    with pytest.raises(ValueError, match="trigger_tpsl must be"):
        _order_to_wire({"asset": 1, "is_buy": True, "size": "1", "price": "1", "trigger_px": "2"})


def test_order_with_inline_tp_sl(exchange, fake_session):
    exchange.order(
        asset=1,
        is_buy=True,
        size="10",
        price="100",
        tp_price="120",
        tp_limit_price="119",
        tp_order_type=1,
        sl_price="90",
        sl_limit_price="89",
        sl_order_type=2,
        sl_size="5",
        sl_trigger_type=1,
    )
    order = _action(fake_session)["orders"][0]
    assert order["tpPrice"] == "120" and order["tpLimitPrice"] == "119" and order["tpOrderType"] == 1
    assert order["tpSize"] == "0" and order["tpTriggerType"] == 0  # 0 = close the whole position, mark price
    assert order["slPrice"] == "90" and order["slOrderType"] == 2
    assert order["slSize"] == "5" and order["slTriggerType"] == 1


def test_inline_tp_sl_requires_order_type(exchange):
    with pytest.raises(ValueError, match="tp_order_type is required"):
        exchange.order(asset=1, is_buy=True, size="1", price="100", tp_price="120", tp_limit_price="119")


def test_inline_tp_sl_rejected_on_reduce_only_order(exchange):
    from upside.exchange import _order_to_wire

    with pytest.raises(ValueError, match="reduce-only order cannot carry inline TP/SL"):
        _order_to_wire(
            {
                "asset": 1,
                "is_buy": False,
                "size": "1",
                "price": "100",
                "reduce_only": True,
                "tp_price": "120",
                "tp_limit_price": "119",
                "tp_order_type": 1,
            }
        )


def test_trigger_order_carries_tp_sl_of_the_order_it_places(exchange, fake_session):
    """Since 2026-09-21 a trigger order's top-level tp*/sl* belong to the order placed on trigger."""
    exchange.trigger_order(
        asset=1,
        is_buy=True,
        size="1",
        price="100",
        trigger_px="99",
        tpsl="tp",
        tp_price="120",
        tp_limit_price="119",
        tp_order_type=1,
    )
    order = _action(fake_session)["orders"][0]
    assert order["t"] == {"trigger": {"triggerPx": "99", "isMarket": True, "tpsl": "tp"}}
    assert order["tpPrice"] == "120" and order["tpLimitPrice"] == "119" and order["tpOrderType"] == 1
    assert "slPrice" not in order


def test_batch_rejects_trigger_and_inline_tpsl(exchange):
    """Both are single-order-only server side: a batch rejects or drops them."""
    with pytest.raises(ValueError, match="trigger order must be sent on its own"):
        exchange.bulk_orders(
            [
                {"asset": 1, "is_buy": True, "size": "1", "price": "1"},
                {"asset": 1, "is_buy": True, "size": "1", "price": "1", "trigger_px": "2", "trigger_tpsl": "sl"},
            ]  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="inline TP/SL is ignored in a batch"):
        exchange.bulk_orders(
            [
                {"asset": 1, "is_buy": True, "size": "1", "price": "1"},
                {"asset": 1, "is_buy": True, "size": "1", "price": "1", "tp_price": "2"},
            ]  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("tif", ["Ioc", "Fok"])
def test_modify_rejects_non_resting_tif(exchange, tif):
    with pytest.raises(ValueError, match="resting tif only"):
        exchange.modify(asset=1, oid=1, tif=tif)  # type: ignore[arg-type]


def test_signature_chain_id_travels_unsigned_at_top_level(fake_session):
    """Browser-wallet path: typed digest uses the override, Agent path ignores it."""
    from upside.utils import signing

    ex = Exchange(PRIVATE_KEY, base_url="https://dev.upsidemax.xyz", signature_chain_id=1)
    ex.session = fake_session

    ex.register_account()
    env = fake_session.last["json"]
    assert env["signatureChainId"] == "0x1"
    assert "signatureChainId" not in env["action"]
    digest = signing.eip712_digest(env["action"], env["nonce"], 1)
    sig = env["signature"]
    assert Account._recover_hash(digest, vrs=(sig["v"], int(sig["r"], 16), int(sig["s"], 16))).lower() == ADDRESS

    ex.cancel_all(1)
    env = fake_session.last["json"]
    digest = signing.eip712_digest(
        env["action"], env["nonce"], chain_id=9767
    )  # Agent path: the environment (Devnet) chain id
    sig = env["signature"]
    assert Account._recover_hash(digest, vrs=(sig["v"], int(sig["r"], 16), int(sig["s"], 16))).lower() == ADDRESS


def test_invalid_param_errors_are_exposed(exchange, fake_session):
    fake_session.queue(
        {
            "status": "error",
            "code": "INVALID_PARAM",
            "message": "s OUT_OF_RANGE",
            "errors": [{"field": "s", "reason": "OUT_OF_RANGE", "expected": "> 0"}],
        },
        400,
    )
    with pytest.raises(ClientError) as exc:
        exchange.order(asset=1, is_buy=True, size="1", price="1", sync=True)
    assert exc.value.errors == [{"field": "s", "reason": "OUT_OF_RANGE", "expected": "> 0"}]
    assert "OUT_OF_RANGE" in str(exc.value)


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
    digest = signing.eip712_digest(env["action"], env["nonce"], chain_id=9767)
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


@pytest.mark.parametrize("price", [0, "0", "-5"])
def test_order_rejects_non_positive_price(exchange, price):
    """p must be > 0; a market order is not price-band checked, so 0 would sweep."""
    with pytest.raises(ValueError, match="price must be a positive"):
        exchange.order(asset=1, is_buy=False, size="5", price=price)


@pytest.mark.parametrize("price", ["abc", "", "1.5"])
def test_order_rejects_non_integer_price(exchange, price):
    """Prices are raw integers; a decimal string means an unscaled value."""
    with pytest.raises(ValueError, match="raw integer string"):
        exchange.order(asset=1, is_buy=False, size="5", price=price)


def test_batch_allows_zeroed_tpsl_legs(exchange, fake_session):
    """ "0" is the not-set convention, so a zeroed leg must not trip the guard."""
    exchange.bulk_orders(
        [
            {"asset": 1, "is_buy": True, "size": "1", "price": "1", "tp_price": "0", "sl_price": "0"},
            {"asset": 1, "is_buy": False, "size": "1", "price": "2"},
        ]  # type: ignore[arg-type]
    )
    orders = _action(fake_session)["orders"]
    assert len(orders) == 2
    assert "tpPrice" not in orders[0]


def test_signature_chain_id_is_omitted_on_agent_path_actions(fake_session):
    """Agent-path actions always sign over the environment's chain id; announcing another would lie."""
    ex = Exchange(PRIVATE_KEY, base_url="https://dev.upsidemax.xyz", signature_chain_id=1)
    ex.session = fake_session
    ex.order(asset=1, is_buy=True, size="1", price="1")
    assert "signatureChainId" not in fake_session.last["json"]
    ex.revoke_agent("0x" + "cd" * 20)  # typed path
    assert fake_session.last["json"]["signatureChainId"] == "0x1"


@pytest.mark.parametrize(
    "base_url, chain_id",
    [
        ("https://upsidemax.xyz", 9788),
        ("https://upsidemax.xyz/", 9788),
        ("upsidemax.xyz", 9788),
        ("https://dev.upsidemax.xyz", 9767),
        ("http://localhost:9090", 9788),
    ],
)
def test_chain_id_follows_base_url(base_url, chain_id):
    assert Exchange(PRIVATE_KEY, base_url=base_url).chain_id == chain_id


def test_explicit_chain_id_wins_over_base_url():
    assert Exchange(PRIVATE_KEY, base_url="https://upsidemax.xyz", chain_id=9767).chain_id == 9767
    assert Exchange(PRIVATE_KEY, base_url="http://localhost:9090", chain_id=9767).chain_id == 9767


def test_default_environment_is_mainnet():
    ex = Exchange(PRIVATE_KEY)
    assert ex.base_url == "https://upsidemax.xyz"
    assert ex.chain_id == 9788


def test_mainnet_signs_both_paths_over_9788(fake_session):
    from upside.utils import signing

    ex = Exchange(PRIVATE_KEY, base_url="https://upsidemax.xyz")
    ex.session = fake_session
    for send in (ex.register_account, lambda: ex.cancel_all(1)):
        send()
        env = fake_session.last["json"]
        assert "signatureChainId" not in env
        digest = signing.eip712_digest(env["action"], env["nonce"], chain_id=9788)
        sig = env["signature"]
        assert Account._recover_hash(digest, vrs=(sig["v"], int(sig["r"], 16), int(sig["s"], 16))).lower() == ADDRESS


# -- envelope sync flag --------------------------------------------------------
SYNC_CALLS = {
    "order": lambda ex, **kw: ex.order(asset=1, is_buy=True, size="1", price="50", **kw),
    "market_order": lambda ex, **kw: ex.market_order(asset=1, is_buy=True, size="1", price="55", **kw),
    "trigger_order": lambda ex, **kw: ex.trigger_order(
        asset=1, is_buy=False, size="1", price="49", trigger_px="50", tpsl="sl", **kw
    ),
    "cancel": lambda ex, **kw: ex.cancel(asset=1, oid=3, **kw),
    "cancel_by_cloid": lambda ex, **kw: ex.cancel_by_cloid(asset=1, cloid=123, **kw),
    "cancel_all": lambda ex, **kw: ex.cancel_all(asset=1, **kw),
    "modify": lambda ex, **kw: ex.modify(asset=1, oid=12345, price="150", **kw),
}


def test_sync_omitted_by_default(exchange, fake_session):
    for call in SYNC_CALLS.values():
        call(exchange)
        assert "sync" not in fake_session.last["json"]


@pytest.mark.parametrize("name", sorted(SYNC_CALLS))
@pytest.mark.parametrize("value", [True, False])
def test_sync_rides_on_the_envelope_unsigned(exchange, fake_session, name, value):
    SYNC_CALLS[name](exchange, sync=value)
    env = fake_session.last["json"]
    assert env["sync"] is value
    assert "sync" not in env["action"]
    # The signature covers the action only, so the same signer is recovered.
    from upside.utils.signing import eip712_digest

    sig = env["signature"]
    vrs = (sig["v"], int(sig["r"], 16), int(sig["s"], 16))
    digest = eip712_digest(dict(env["action"]), env["nonce"], chain_id=9767)
    assert Account._recover_hash(digest, vrs=vrs).lower() == ADDRESS


def test_instance_sync_default_and_per_call_override(fake_session):
    ex = Exchange(PRIVATE_KEY, base_url="https://dev.upsidemax.xyz", sync=True)
    ex.session = fake_session
    ex.cancel(asset=1, oid=3)
    assert fake_session.last["json"]["sync"] is True
    ex.cancel(asset=1, oid=3, sync=False)
    assert fake_session.last["json"]["sync"] is False


def test_instance_sync_not_sent_on_other_actions(fake_session):
    """Any other action rejects the flag outright (400 SYNC_NOT_APPLICABLE)."""
    ex = Exchange(PRIVATE_KEY, base_url="https://dev.upsidemax.xyz", sync=True)
    ex.session = fake_session
    ex.update_leverage(asset=1, leverage=5)
    ex.tp_sl(asset=1, tp_price="90000", tp_limit_price="90000", tp_order_type=1)
    ex.cancel_conditional(oid=1)
    ex.lock_collateral(1, 1, "1000")
    assert all("sync" not in r["json"] for r in fake_session.requests)


def test_sync_must_be_a_bool(exchange):
    with pytest.raises(TypeError):
        exchange.cancel(asset=1, oid=3, sync="true")  # type: ignore[arg-type]


# -- local validation: the async path drops malformed actions without a trace ----
@pytest.mark.parametrize(
    "call",
    [
        lambda ex: ex.order(asset=1, is_buy=True, size="0", price="50"),
        lambda ex: ex.order(asset=1, is_buy=True, size="-5", price="50"),
        lambda ex: ex.order(asset=1, is_buy=True, size="1.5", price="50"),
        lambda ex: ex.order(asset=1, is_buy=True, size="abc", price="50"),
        lambda ex: ex.order(asset=1, is_buy=True, size="1", price="50", tif="Day"),  # type: ignore[arg-type]
        lambda ex: ex.order(asset=1, is_buy=1, size="1", price="50"),  # type: ignore[arg-type]
        lambda ex: ex.order(asset=0, is_buy=True, size="1", price="50"),
        lambda ex: ex.order(asset=True, is_buy=True, size="1", price="50"),
        lambda ex: ex.trigger_order(asset=1, is_buy=True, size="1", price="50", trigger_px="0", tpsl="sl"),
        lambda ex: ex.modify(asset=1, oid=1, price="0"),
        lambda ex: ex.modify(asset=1, oid=1, size="-1"),
        lambda ex: ex.cancel(asset=1, oid="x"),
        lambda ex: ex.cancel(asset=1, oid=0),
        lambda ex: ex.cancel_conditional(oid=-3),
    ],
)
def test_malformed_fields_never_leave_the_client(exchange, fake_session, call):
    with pytest.raises((ValueError, TypeError)):
        call(exchange)
    assert fake_session.requests == []


def test_string_ids_from_info_are_sent_as_numbers(exchange, fake_session):
    """/info returns ids as strings; the actions only accept JSON numbers."""
    exchange.cancel(asset="1", oid="8280")
    assert _action(fake_session)["cancels"] == [{"a": 1, "o": 8280}]
    exchange.modify(asset="1", oid="8280", price="150")
    assert _action(fake_session)["a"] == 1 and _action(fake_session)["oid"] == 8280
    exchange.cancel_conditional(oid="734796988633055604")
    assert _action(fake_session)["oid"] == 734796988633055604
    exchange.order(asset="2", is_buy=True, size=3, price=40)
    assert _action(fake_session)["orders"][0]["a"] == 2
    assert _action(fake_session)["orders"][0]["s"] == "3"


def test_standalone_tp_sl_rules(exchange, fake_session):
    with pytest.raises(ValueError, match="order_side"):
        exchange.tp_sl(asset=1, sl_price="80", sl_limit_price="79", sl_order_type=1, is_position_tpsl=False)
    with pytest.raises(ValueError, match="exactly one"):
        exchange.tp_sl(
            asset=1,
            is_position_tpsl=False,
            order_side="B",
            tp_price="95",
            tp_limit_price="94",
            tp_order_type=1,
            sl_price="80",
            sl_limit_price="79",
            sl_order_type=1,
        )
    with pytest.raises(ValueError, match="standalone"):
        exchange.tp_sl(
            asset=1,
            tp_price="95",
            tp_limit_price="94",
            tp_order_type=1,
            open_sl_price="70",
            open_sl_limit_price="69",
            open_sl_order_type=2,
        )
    assert fake_session.requests == []


def test_standalone_tp_sl_with_open_legs_matches_the_documented_example(exchange, fake_session):
    exchange.tp_sl(
        asset=1,
        is_position_tpsl=False,
        order_side="B",
        sl_price="80000",
        sl_limit_price="79900",
        sl_size="1",
        sl_order_type=1,
        open_tp_price="95000",
        open_tp_limit_price="94900",
        open_tp_order_type=1,
        open_sl_price="70000",
        open_sl_limit_price="69900",
        open_sl_order_type=2,
    )
    action = _action(fake_session)
    assert action["orderSide"] == "B" and action["isPositionTpsl"] is False
    assert (action["slPrice"], action["slLimitPrice"], action["slSize"], action["slOrderType"]) == (
        "80000",
        "79900",
        "1",
        1,
    )
    assert action["openTpPrice"] == "95000" and action["openTpLimitPrice"] == "94900"
    assert action["openTpSize"] == "0" and action["openTpOrderType"] == 1 and action["openTpTriggerType"] == 0
    assert action["openSlPrice"] == "70000" and action["openSlOrderType"] == 2


def test_open_leg_needs_its_order_type(exchange):
    with pytest.raises(ValueError, match="open_tp_order_type"):
        exchange.tp_sl(
            asset=1,
            is_position_tpsl=False,
            order_side="B",
            sl_price="80",
            sl_limit_price="79",
            sl_order_type=1,
            open_tp_price="95",
            open_tp_limit_price="94",
        )


def test_register_into_a_market_deployer(exchange, fake_session):
    exchange.register_account(invite_code="X", market_deployer_id=2)
    assert _action(fake_session) == {"type": "registerAccount", "address": ADDRESS, "marketDeployerId": 2}
    exchange.register_account(market_deployer_id=0)
    assert _action(fake_session)["marketDeployerId"] == 0
    exchange.register_account()
    assert "marketDeployerId" not in _action(fake_session)


def test_register_rejects_a_foreign_address(exchange, fake_session):
    with pytest.raises(ValueError, match="own address"):
        exchange.register_account(address="0x" + "ab" * 20)
    assert fake_session.requests == []


@pytest.mark.parametrize("field", ["size", "price"])
def test_amount_above_int64_rejected(exchange, field):
    kwargs = {"asset": 1, "is_buy": True, "size": "1", "price": "50"}
    kwargs[field] = str(2**63)
    with pytest.raises(ValueError, match=field):
        exchange.order(**kwargs)


@pytest.mark.parametrize("value", ["²", "١٢", "--5"])
def test_non_ascii_or_malformed_digits_rejected(exchange, value):
    with pytest.raises(ValueError, match="raw integer string"):
        exchange.order(asset=1, is_buy=True, size=value, price="50")
