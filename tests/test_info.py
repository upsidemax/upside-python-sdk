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
