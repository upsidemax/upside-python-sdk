"""Info query tests: request bodies and error mapping (fake session, no network)."""

import pytest

from upside import Info
from upside.utils.error import ServerError


@pytest.fixture
def info(fake_session):
    client = Info(base_url="https://dev.upsidemax.xyz", skip_ws=True)
    client.session = fake_session
    return client


def body(fake_session):
    return fake_session.last["json"]


def test_configs_request(info, fake_session):
    info.configs()
    assert body(fake_session) == {"type": "configs", "marketDeployerId": 0}
    assert fake_session.last["url"] == "https://dev.upsidemax.xyz/info"


def test_l2_book_stringifies_asset(info, fake_session):
    info.l2_book(23)
    assert body(fake_session) == {"type": "l2Book", "asset": "23"}


def test_market_state_request(info, fake_session):
    info.market_state("1")
    assert body(fake_session) == {"type": "marketState", "asset": "1"}


def test_candle_snapshot_optional_times(info, fake_session):
    info.candle_snapshot(1, "1m")
    assert body(fake_session) == {"type": "candleSnapshot", "asset": "1", "interval": "1m"}
    info.candle_snapshot(1, "1h", start_time=100, end_time=200)
    assert body(fake_session)["startTime"] == 100
    assert body(fake_session)["endTime"] == 200


def test_user_account_request(info, fake_session):
    info.user_account(5, 1)
    assert body(fake_session) == {"type": "userAccount", "accountId": "5", "marketDeployerId": 1}


def test_user_orders_request(info, fake_session):
    info.user_orders(5, 1, contract_id=1)
    assert body(fake_session) == {
        "type": "userOrders",
        "accountId": "5",
        "marketDeployerId": 1,
        "contractId": 1,
    }


def test_orders_by_ids_stringifies(info, fake_session):
    info.orders_by_ids(1, [8280, "8281"])
    assert body(fake_session)["orderIds"] == ["8280", "8281"]


def test_orders_by_cloids_stringifies(info, fake_session):
    info.orders_by_cloids(1002, 1, [1778844423064])
    assert body(fake_session)["cloids"] == ["1778844423064"]


def test_error_response_raises(info, fake_session):
    fake_session.queue({"status": "error", "code": "UNKNOWN_TYPE", "message": "nope"}, 400)
    with pytest.raises(Exception) as exc:
        info.configs()
    assert exc.value.code == "UNKNOWN_TYPE"  # type: ignore[attr-defined]


def test_non_json_body_raises_server_error(info, fake_session):
    fake_session.queue("<html>oops</html>", 200)
    with pytest.raises(ServerError):
        info.configs()


def test_share_group_state_request(info, fake_session):
    info.share_group_state(3)
    assert body(fake_session) == {"type": "shareGroupState", "groupId": 3}


def test_user_market_deployers_request(info, fake_session):
    info.user_market_deployers(5)
    assert body(fake_session) == {"type": "userMarketDeployers", "accountId": "5"}


def test_user_agents_request(info, fake_session):
    info.user_agents(5)
    assert body(fake_session) == {"type": "userAgents", "accountId": "5"}


def test_subscribe_requires_ws(info):
    with pytest.raises(RuntimeError):
        info.subscribe({"type": "config"}, lambda m: None)


def test_ws_authenticate_requires_ws(info):
    with pytest.raises(RuntimeError):
        info.ws_authenticate(3)


def test_unsubscribe_requires_ws(info):
    with pytest.raises(RuntimeError):
        info.unsubscribe({"type": "config"}, 1)


def test_close_is_safe_without_ws(info):
    info.close()  # skip_ws=True, must not raise


def test_ticker_single_asset(info, fake_session):
    info.ticker(10000001)
    assert body(fake_session) == {"type": "ticker", "asset": "10000001"}


def test_ticker_omits_asset_for_all_markets(info, fake_session):
    info.ticker()
    assert body(fake_session) == {"type": "ticker"}


def test_account_by_address_lowercases(info, fake_session):
    info.account_by_address("0xABC0000000000000000000000000000000000001")
    assert body(fake_session) == {
        "type": "accountByAddress",
        "address": "0xabc0000000000000000000000000000000000001",
    }


@pytest.mark.parametrize(
    "method,query_type",
    [
        ("user_fills", "userFills"),
        ("order_history", "orderHistory"),
        ("user_funding_flows", "userFundingFlows"),
    ],
)
def test_history_queries_minimal(info, fake_session, method, query_type):
    getattr(info, method)(5)
    assert body(fake_session) == {"type": query_type, "accountId": "5", "contractId": 0}


@pytest.mark.parametrize("method", ["user_fills", "order_history", "user_funding_flows"])
def test_history_queries_full_page(info, fake_session, method):
    getattr(info, method)(5, contract_id=1, start_time=100, end_time=200, limit=50)
    sent = body(fake_session)
    assert sent["contractId"] == 1
    assert sent["startTime"] == 100 and sent["endTime"] == 200
    assert sent["limit"] == 50


@pytest.mark.parametrize("limit", [0, 1001])
def test_history_rejects_bad_limit(info, limit):
    with pytest.raises(ValueError, match="limit must be"):
        info.user_fills(5, limit=limit)


def test_history_rejects_inverted_window(info):
    """The server answers 400; catch it before the round trip."""
    with pytest.raises(ValueError, match="start_time must not be after end_time"):
        info.order_history(5, start_time=200, end_time=100)


def test_configs_filters_by_market_deployer(info, fake_session):
    info.configs(2)
    assert body(fake_session) == {"type": "configs", "marketDeployerId": 2}


def test_user_account_accepts_zero_deployer_for_account_wide_view(info, fake_session):
    info.user_account("5", 0)
    assert body(fake_session) == {"type": "userAccount", "accountId": "5", "marketDeployerId": 0}


def test_string_entries_in_errors_do_not_break_the_exception(info, fake_session):
    """A malformed errors[] must not cost the caller the whole error envelope."""
    from upside.utils.error import ClientError

    fake_session.queue(
        {"status": "error", "code": "INVALID_PARAM", "message": "bad", "errors": ["s OUT_OF_RANGE"]}, 400
    )
    with pytest.raises(ClientError) as exc:
        info.configs()
    assert exc.value.code == "INVALID_PARAM"
    assert exc.value.errors == []
