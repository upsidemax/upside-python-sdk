"""Timeouts and retries: /info is resent on transient failures, /exchange only
when the request never left the machine.

The classification tests drive real sockets (a closed port, a server that never
answers) so they pin what ``requests`` / ``urllib3`` actually raise, not what we
assume they raise.
"""

import socket
import threading
from typing import Iterator, List

import pytest
import requests

from upside import Exchange, Info, OutcomeUnknownError
from upside.api import DEFAULT_TIMEOUT, _never_sent
from upside.utils.error import ServerError

PRIVATE_KEY = "0x" + "01" * 32
OK = {"status": "ok", "response": {"data": {}}}


def _no_sleep(client) -> List[float]:
    slept: List[float] = []
    client._sleep = slept.append
    return slept


@pytest.fixture
def info(fake_session):
    client = Info(base_url="https://dev.upsidemax.xyz", skip_ws=True)
    client.session = fake_session
    return client


@pytest.fixture
def exchange(fake_session):
    ex = Exchange(PRIVATE_KEY, base_url="https://dev.upsidemax.xyz")
    ex.session = fake_session
    return ex


# -- defaults ----------------------------------------------------------------
def test_requests_carry_a_default_timeout(info, fake_session):
    info.configs()
    assert fake_session.last["timeout"] == DEFAULT_TIMEOUT


def test_explicit_timeout_is_used(fake_session):
    client = Info(base_url="https://dev.upsidemax.xyz", skip_ws=True, timeout=3.0)
    client.session = fake_session
    client.configs()
    assert fake_session.last["timeout"] == 3.0


def test_negative_max_retries_rejected():
    with pytest.raises(ValueError):
        Info(skip_ws=True, max_retries=-1)


# -- /info -------------------------------------------------------------------
def test_info_retries_read_timeout(info, fake_session):
    slept = _no_sleep(info)
    fake_session.queue_error(requests.exceptions.ReadTimeout("read timed out"))
    fake_session.queue_error(requests.exceptions.ConnectionError("reset"))
    fake_session.queue({"type": "configs"})
    assert info.configs() == {"type": "configs"}
    assert len(fake_session.requests) == 3
    assert len(slept) == 2 and slept[1] > slept[0] * 0.9  # backoff grows (jittered)


def test_info_gives_up_after_max_retries(info, fake_session):
    _no_sleep(info)
    for _ in range(10):
        fake_session.queue_error(requests.exceptions.ReadTimeout("read timed out"))
    with pytest.raises(requests.exceptions.ReadTimeout):
        info.configs()
    assert len(fake_session.requests) == 1 + info.max_retries


def test_info_max_retries_zero_disables_retry(fake_session):
    client = Info(base_url="https://dev.upsidemax.xyz", skip_ws=True, max_retries=0)
    client.session = fake_session
    fake_session.queue_error(requests.exceptions.ReadTimeout("read timed out"))
    with pytest.raises(requests.exceptions.ReadTimeout):
        client.configs()
    assert len(fake_session.requests) == 1


@pytest.mark.parametrize(
    "status, body",
    [
        (503, {"status": "error", "code": "DOWNSTREAM_UNAVAILABLE", "message": "busy"}),
        (504, {"status": "error", "code": "DOWNSTREAM_TIMEOUT", "message": "slow"}),
        (502, "<html>Bad Gateway</html>"),
    ],
)
def test_info_retries_transient_status(info, fake_session, status, body):
    _no_sleep(info)
    fake_session.queue(body, status)
    fake_session.queue({"type": "userFills", "fills": []})
    assert info.user_fills(5)["type"] == "userFills"
    assert len(fake_session.requests) == 2


def test_info_not_ready_is_not_retried(info, fake_session):
    _no_sleep(info)
    fake_session.queue({"status": "error", "code": "NOT_READY", "message": "no book"}, 503)
    with pytest.raises(ServerError) as err:
        info.l2_book(1)
    assert err.value.code == "NOT_READY"
    assert len(fake_session.requests) == 1


def test_info_transient_status_surfaces_after_retries(info, fake_session):
    _no_sleep(info)
    for _ in range(10):
        fake_session.queue({"status": "error", "code": "DOWNSTREAM_TIMEOUT", "message": "slow"}, 504)
    with pytest.raises(ServerError) as err:
        info.order_history(5)
    assert err.value.status_code == 504
    assert len(fake_session.requests) == 1 + info.max_retries


# -- /exchange ---------------------------------------------------------------
def test_exchange_read_timeout_is_not_resent(exchange, fake_session):
    _no_sleep(exchange)
    fake_session.queue_error(requests.exceptions.ReadTimeout("read timed out"))
    with pytest.raises(OutcomeUnknownError) as err:
        exchange.order(asset=1, is_buy=True, size="1", price="50")
    assert len(fake_session.requests) == 1
    assert err.value.action_type == "order"
    assert err.value.nonce == fake_session.last["json"]["nonce"]
    assert isinstance(err.value.__cause__, requests.exceptions.ReadTimeout)


def test_exchange_connection_dropped_mid_flight_is_not_resent(exchange, fake_session):
    _no_sleep(exchange)
    fake_session.queue_error(requests.exceptions.ConnectionError("Connection aborted"))
    with pytest.raises(OutcomeUnknownError):
        exchange.cancel(asset=1, oid=7)
    assert len(fake_session.requests) == 1


def test_exchange_connect_timeout_resends_the_same_envelope(exchange, fake_session):
    _no_sleep(exchange)
    fake_session.queue_error(requests.exceptions.ConnectTimeout("connect timed out"))
    fake_session.queue(OK)
    exchange.update_leverage(asset=1, leverage=5)
    first, second = fake_session.requests
    assert first["json"] == second["json"]  # never delivered, so the same nonce is fine


def test_exchange_http_error_is_not_retried(exchange, fake_session):
    _no_sleep(exchange)
    fake_session.queue({"status": "error", "code": "DOWNSTREAM_UNAVAILABLE", "message": "busy"}, 503)
    with pytest.raises(ServerError):
        exchange.order(asset=1, is_buy=True, size="1", price="50")
    assert len(fake_session.requests) == 1


# -- classification against real sockets --------------------------------------
def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def silent_server() -> Iterator[int]:
    """Accepts connections and reads the request, but never answers."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    held: List[socket.socket] = []
    stop = threading.Event()

    def serve() -> None:
        srv.settimeout(0.1)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                held.append(conn)
            except OSError:
                continue

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield srv.getsockname()[1]
    stop.set()
    thread.join()
    for conn in held:
        conn.close()
    srv.close()


def test_refused_connection_counts_as_never_sent():
    with pytest.raises(requests.exceptions.ConnectionError) as err:
        requests.post(f"http://127.0.0.1:{_closed_port()}/exchange", json={}, timeout=2)
    assert _never_sent(err.value)


def test_read_timeout_counts_as_maybe_sent(silent_server):
    with pytest.raises(requests.exceptions.ReadTimeout) as err:
        requests.post(f"http://127.0.0.1:{silent_server}/exchange", json={}, timeout=(2, 0.2))
    assert not _never_sent(err.value)


def test_exchange_against_silent_server_raises_outcome_unknown(silent_server):
    ex = Exchange(PRIVATE_KEY, base_url=f"http://127.0.0.1:{silent_server}", timeout=(2, 0.2))
    _no_sleep(ex)
    with pytest.raises(OutcomeUnknownError):
        ex.cancel(asset=1, oid=1)


def test_info_against_silent_server_retries_then_raises(silent_server):
    client = Info(base_url=f"http://127.0.0.1:{silent_server}", skip_ws=True, timeout=(2, 0.2), max_retries=2)
    slept = _no_sleep(client)
    with pytest.raises(requests.exceptions.ReadTimeout):
        client.configs()
    assert len(slept) == 2


def test_exchange_refused_connection_is_retried():
    ex = Exchange(PRIVATE_KEY, base_url=f"http://127.0.0.1:{_closed_port()}", max_retries=2)
    slept = _no_sleep(ex)
    with pytest.raises(requests.exceptions.ConnectionError):
        ex.cancel(asset=1, oid=1)
    assert len(slept) == 2  # safe to resend: nothing was delivered
