# UpsideMAX Python SDK

A Python client for the [UpsideMAX](https://docs.upsidemax.xyz) perpetuals
exchange — REST reads (`POST /info`), signed writes (`POST /exchange`),
and realtime WebSocket streams. Authentication is EIP-712 wallet-signed
(secp256k1) rather than API keys.

- **EIP-712 request signing** (secp256k1) with the Agent and Typed paths — no API keys.
- **Synchronous REST** over `requests`, **threaded WebSocket** over `websocket-client`.
- Raw-dict responses, `TypedDict` inputs, full type hints (ships `py.typed`).
- Agent (API-wallet) delegation, TP/SL, leverage/margin, and collateral actions.

> The default environment is the **UpsideMAX Devnet** (`https://dev.upsidemax.xyz`,
> `constants.DEVNET_API_URL`).
> Contract IDs, scales, and tick/step sizes are server-assigned — always read
> them from `configs`, never hardcode.

## Installation

```bash
pip install upside-python-sdk
```

Requires Python 3.9+. Runtime dependencies: `requests`, `websocket-client`,
`eth-account`, `eth-utils`.

## Quick start

```python
from upside import Info, Exchange
from upside.utils import constants

# --- reads (no signing) ---
info = Info(base_url=constants.DEVNET_API_URL)
cfg = info.configs()
contract = next(c for c in cfg["contracts"] if c["status"] == "Active")
asset = contract["contractId"]
print(info.market_state(asset))

# --- writes (EIP-712 signed) ---
exchange = Exchange("0x<private-key>", base_url=constants.DEVNET_API_URL)

# Register (Devnet requires an invite code from the UpsideMAX team). A test airdrop
# follows once this call has returned 200.
exchange.register_account(invite_code="<invite-code>")

# Place a resting limit buy. Prices/sizes are raw integer strings — scale them
# with the contract's priceScale / qtyScale from configs.
exchange.order(asset=asset, is_buy=True, size="10", price="50")
```

## Reading data — `Info`

All methods return the raw parsed JSON. See
[docs.upsidemax.xyz/info](https://docs.upsidemax.xyz/info/overview) for response shapes.

```python
info.configs()                                  # contracts, coins, scales, tiers (cache this)
info.l2_book(asset)                             # full order book snapshot
info.market_state(asset)                        # mark/oracle/last price, funding
info.candle_snapshot(asset, "1m", start, end)   # historical OHLCV
info.user_account(account_id, market_deployer_id)
info.user_orders(account_id, market_deployer_id=0, contract_id=0)   # 0 = all; check "truncated"
info.orders_by_ids(market_deployer_id, ["8280"])
info.orders_by_cloids(account_id, market_deployer_id, ["1778844423064"])
info.ticker(asset)                              # 24h rolling stats (omit asset = all markets)
info.user_agents(account_id)
info.user_market_deployers(account_id)
info.account_by_address("0x<address>")          # master or agent address -> accountId
info.share_group_state()

# history (ascending, paginate on the last row's time; limit <= 1000)
info.user_fills(account_id, contract_id=0, start_time=None, end_time=None, limit=None)
info.order_history(account_id)                  # terminal orders only; active ones are in user_orders
info.user_funding_flows(account_id)
```

## Trading — `Exchange`

```python
from upside import Cloid

exchange.order(asset=1, is_buy=True, size="10", price="50", cloid=Cloid.from_int(1001))
exchange.market_order(asset=1, is_buy=False, size="5", price="61000")  # price = execution price to cross to
exchange.bulk_orders([...])                     # up to 10 orders, one signature
exchange.cancel(asset=1, oid=15)
exchange.cancel_by_cloid(asset=1, cloid=1001)
exchange.cancel_all(asset=1)
exchange.modify(asset=1, oid=15, price="151", size="8")

# conditional order: fires when the mark price crosses trigger_px
exchange.trigger_order(asset=1, is_buy=False, size="10", price="79000", trigger_px="80000", tpsl="sl")

# entry-inline TP/SL: promoted to position TP/SL once this order fills completely
exchange.order(asset=1, is_buy=True, size="10", price="100",
               tp_price="120", tp_limit_price="119", tp_order_type=1,   # 1 = limit, 2 = market
               sl_price="90", sl_limit_price="89", sl_order_type=2)

exchange.update_leverage(asset=1, leverage=20)
exchange.update_margin_mode(asset=1, is_cross=False)   # HEDGE is disabled server-side; ONE_WAY only
exchange.update_isolated_margin(asset=1, ntli=5000)
exchange.update_slippage_setting(market_deployer_id=1, market_slippage_bps=500)

exchange.tp_sl(asset=1, tp_price="90000", tp_limit_price="90000", tp_order_type=1)
exchange.cancel_tp_sl(asset=1)
exchange.cancel_conditional(oid=123)

exchange.lock_collateral(market_deployer_id=1, coin_id=1, amount="1000")
exchange.unlock_collateral(market_deployer_id=1, coin_id=1, amount="1000")
exchange.transfer_between_deployers(1, 2, coin_id=1, amount="1000")

# portfolio (shared) margin
exchange.set_margin_share_type(1)                      # 0 = UNIFIED, 1 = PORTFOLIO
exchange.transfer_md_to_share_group(1, group_id=3, coin_id=1, amount="1000")
exchange.transfer_share_group_to_md(3, market_deployer_id=1, coin_id=1, amount="1000")
exchange.lock_into_share_group(group_id=3, coin_id=1, amount="1000")
exchange.unlock_from_share_group(group_id=3, coin_id=1, amount="1000")
```

Market orders and market TP/SL legs carry an **execution price** you compute
yourself (`mark price ± marketSlippageBps/1e4`) — the server derives none. Read
your account's cap from `Info.user_account`'s `marketSlippageBps` and set it
with `update_slippage_setting`.

### Orders and cancels: pick `sync`

The order/cancel family (`order`, `cancel`, `cancelByCloid`, `cancelAll`,
`modify`) has two receipt shapes, chosen per request with `sync` (an unsigned
envelope flag — the signature is the same either way):

```python
exchange = Exchange(key, sync=True)                    # default for this client
exchange.order(asset=1, is_buy=True, size="10", price="50")
exchange.cancel(asset=1, oid=15, sync=False)           # per-call override
```

- `sync=True` — HTTP 200 once the action has executed, with
  `response.data.statuses[]` holding one entry per submitted item in request
  order. `order` / `modify` (`modify`: exactly one) entries are
  `{"resting": {"oid"}}`, `{"filled": {"totalSz", "avgPx", "oid"}}` or
  `{"error": "..."}`; `cancel` / `cancelByCloid` entries are `"success"` or
  `{"error": "..."}`. `cancelAll` returns
  `{"limitCancelled": n, "conditionalCancelled": m}` instead (no `statuses`).
  A malformed request is refused with `400 INVALID_PARAM` (`ClientError`).
- `sync=False` — HTTP 202 straight away with
  `{"status": "accepted", "response": {"type": "accepted", "data": {"count": 1}}}`;
  the per-item outcome arrives on the `orderUpdates` channel, correlated by
  `cloid`, or by `n` (your nonce) + `si` (index within the batch). A malformed
  request may still be answered 202 here and then produce no `orderUpdates`
  entry, so the SDK checks every field (ids, sizes, prices, TIF, types) before
  signing and raises `ValueError` / `TypeError` instead.
- not set — the server's default (currently 202). Set it explicitly if you parse
  the response.

A trigger order answers (with `sync=True`) with the TP/SL receipt
`{"type": "tpSl", "data": {"tpOrderId": n, "slOrderId": n}}`. Every other action
is always synchronous and rejects `sync`, so the SDK only sends it on these five.

Client order ids (`cloid`) are positive int64 decimal strings; `Cloid` and every
`cloid=` argument enforce that before signing.

### Timeouts and retries

Every request has a timeout (`(5, 10)` seconds connect/read by default; pass
`timeout=`). Failures are retried with exponential backoff (`max_retries=3`,
`backoff=0.5` s) **only where resending is safe**:

- **`/info`** — read-only, so a read timeout, a dropped connection, or a
  transient `502` / `503` / `504` is retried. `503 NOT_READY` (no order book yet)
  is raised at once. If every attempt fails, the `requests` exception (or
  `ServerError`) propagates.
- **`/exchange`** — retried only when the request never left your machine
  (connection refused, DNS failure, connect timeout). After that, resending the
  same signed envelope reuses its nonce and is rejected with `DUPLICATE_NONCE`
  *even if the first attempt executed*, so a read timeout raises
  `OutcomeUnknownError` (carrying `nonce` and `action_type`) instead. A `504
  DOWNSTREAM_TIMEOUT` on a `sync=True` request means the same thing. Confirm the
  outcome before signing a new request: `user_orders` for orders still resting
  **and** `order_history` / `user_fills` for ones that already filled or were
  canceled (or the `orderUpdates` stream). `orders_by_cloids` alone is not
  enough — it only sees live orders. History is indexed a moment after
  execution, so re-check for a few seconds before concluding it did not land.

```python
import time

from upside import OutcomeUnknownError

sent_at_ms = int(time.time() * 1000)
try:
    exchange.order(asset=1, is_buy=True, size="10", price="50", cloid=cloid, sync=True)
except OutcomeUnknownError:
    # Did it land? Live orders are in user_orders, finished ones in order_history.
    live = info.user_orders(account_id)["orders"]
    done = info.order_history(account_id, start_time=sent_at_ms)["orders"]
    landed = any(o.get("clientOrderId") == str(cloid) for o in live + done)
```

### HTTP 200 ≠ success

Gateway failures (bad signature, reused nonce, invalid parameter) raise
`ClientError` (4xx) / `ServerError` (5xx); an `INVALID_PARAM` rejection lists the
offending fields in `.errors`. Business rejections come back as HTTP 200 — a per-item
`error` string in `statuses[]`, or a non-zero `errorCode` in `response.data`.
Always inspect them.

## Agent (API-wallet) delegation

Keep the master key offline; authorize a hot agent key to sign trades. The
server routes agent-signed actions to the master account.

```python
response, agent_key = master.approve_agent(agent_name="bot1")   # generates a fresh key
agent = Exchange(agent_key, base_url=constants.DEVNET_API_URL, account_id=master.account_id)
agent.order(asset=1, is_buy=True, size="10", price="50")
master.revoke_agent(agent.address)
```

## WebSocket streams

```python
info = Info(base_url=constants.DEVNET_API_URL)          # WS starts automatically

sid = info.subscribe({"type": "l2Book", "asset": "1"}, lambda m: print(m["data"]["levels"][0][:1]))
info.subscribe({"type": "l2Book", "asset": "1", "group": 1000}, print)  # a price grouping from configs digitMerge
info.subscribe({"type": "trades", "asset": "1"}, print)
info.subscribe({"type": "orderUpdates", "user": "0x<address>"}, print)   # private: pass the wallet address
info.subscribe({"type": "userFills", "user": "0x<address>"}, print)
info.subscribe({"type": "userAccount", "user": "0x<address>", "marketDeployerId": 1}, print)  # every 3s

info.unsubscribe({"type": "l2Book", "asset": "1"}, sid)
info.close()
```

Channels: `l2Book`, `bbo`, `trades`, `candle`, `ticker`, `allMarkets`, `config`
(public) and `orderUpdates`, `openOrders`, `userFills`, `userAccount`
(per-address; `userAccount` also takes `marketDeployerId`, since the account view
differs per deployer). `l2Book` optionally takes `group`, one of the contract's
`digitMerge` values; each grouping is a separate stream, and a `GroupRemoved`
frame (the grouping was dropped from the config) ends it. The client pings every
30s and auto-reconnects, replaying subscriptions.

Several channels open with a snapshot frame whose shape differs from the
increments that follow — `openOrders` (`userOrders`'s response body),
`orderUpdates` / `userFills` (recent history rows in REST's long field names
rather than the compact wire ones), and `trades` (recent trades, flagged
`isSnapshot`). `candle` has no snapshot frame: fetch history with
`candle_snapshot` and apply the stream on top. Dispatch handles the routing;
your callback still has to read both shapes.

`asset` may be given as an int; it is sent as the string the server requires.
A refused subscription comes back as an `error` frame that does not say which
subscription it was — pass `Info(ws_error_callback=...)` to see those (and
failed `AuthResult` frames). Callbacks run on the socket's reader thread, so
keep them short.

## Signing

Every `/exchange` write is authorized by an EIP-712 signature over a fixed
domain (`Exchange` / `1` / chainId `9767` / zero verifying contract). The SDK
handles both paths automatically:

- **Typed path** — `registerAccount`, `approveAgent`, `revokeAgent`,
  `lockCollateral`, `unlockCollateral`, `transferBetweenDeployers`.
- **Agent path** — every other action (canonical-JSON `actionHash`).

Nonces are strictly increasing millisecond timestamps managed per `Exchange`
instance (`NonceManager`). Browser-extension wallets sign typed structs with
their active chain instead of 9767; pass `Exchange(..., signature_chain_id=...)`
to match that and the SDK sends the unsigned top-level `signatureChainId` the
server needs to rebuild the domain.

See
[docs.upsidemax.xyz/guide/authentication](https://docs.upsidemax.xyz/guide/authentication).

## Examples

Runnable scripts live in [`examples/`](examples). Copy `config.json.example` to
`config.json`, set your test wallet and invite code, then:

```bash
python examples/01_register_and_airdrop.py
python examples/03_place_and_cancel_order.py
python examples/06_websocket_streams.py
```

## Development

```bash
make install     # poetry install
make test        # pytest
make lint        # black --check + ruff
make typecheck   # mypy
make check       # all of the above
```

## License

MIT — see [LICENSE](LICENSE).
