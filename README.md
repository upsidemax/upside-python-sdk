# Upside Python SDK

A Python client for the [Upside](https://docs.upsidemax.xyz) perpetuals
exchange — REST reads (`POST /info`), signed writes (`POST /exchange`),
and realtime WebSocket streams. Authentication is EIP-712 wallet-signed
(secp256k1) rather than API keys.

- **EIP-712 request signing** (secp256k1) with the Agent and Typed paths — no API keys.
- **Synchronous REST** over `requests`, **threaded WebSocket** over `websocket-client`.
- Raw-dict responses, `TypedDict` inputs, full type hints (ships `py.typed`).
- Agent (API-wallet) delegation, TP/SL, leverage/margin, and collateral actions.

> The default environment is the **UAT testnet** (`https://dev.upsidemax.xyz`).
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
info = Info(base_url=constants.UAT_API_URL)
cfg = info.configs()
contract = next(c for c in cfg["contracts"] if c["status"] == "Active")
asset = contract["contractId"]
print(info.market_state(asset))

# --- writes (EIP-712 signed) ---
exchange = Exchange("0x<private-key>", base_url=constants.UAT_API_URL)

# Register (UAT requires an invite code from the Upside team). A 10,000 USDC
# test airdrop lands within ~10s.
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
info.user_orders(account_id, market_deployer_id, contract_id=0)
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

### Orders and cancels answer 200 *or* 202

The order/cancel family (`order`, `cancel`, `cancelByCloid`, `cancelAll`,
`modify`) returns **either** HTTP 200 with a `statuses[]` entry per submitted
item (`resting` / `filled` / `error`), **or** HTTP 202 with
`{"status": "accepted", "response": {"type": "accepted", "data": {"count": n}}}`
— where `type` is the literal `"accepted"`, not the action name. Handle both.
On 202 the per-item outcome arrives on the `orderUpdates` channel; correlate by
`cloid`, or by `n` (your nonce) + `si` (index within the batch). A trigger order
is the exception: it answers with the TP/SL receipt
`{"type": "tpSl", "data": {"tpOrderId": n, "slOrderId": n}}`. Every other action
responds synchronously.

### HTTP 200 ≠ success

Gateway failures (bad signature, reused nonce, rate limit) raise `ClientError`
(4xx) / `ServerError` (5xx). Business rejections come back as HTTP 200 — a per-item
`error` string in `statuses[]`, or a non-zero `errorCode` in `response.data`.
Always inspect them.

## Agent (API-wallet) delegation

Keep the master key offline; authorize a hot agent key to sign trades. The
server routes agent-signed actions to the master account.

```python
response, agent_key = master.approve_agent(agent_name="bot1")   # generates a fresh key
agent = Exchange(agent_key, base_url=constants.UAT_API_URL, account_id=master.account_id)
agent.order(asset=1, is_buy=True, size="10", price="50")
master.revoke_agent(agent.address)
```

## WebSocket streams

```python
info = Info(base_url=constants.UAT_API_URL)          # WS starts automatically

sid = info.subscribe({"type": "l2Book", "asset": "1"}, lambda m: print(m["data"]["bookVersion"]))
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
differs per deployer). The client pings every 30s and auto-reconnects, replaying
subscriptions.

Several channels open with a snapshot frame whose shape differs from the
increments that follow — `candle` (a batch of bars under `asset`/`interval`),
`openOrders` (`userOrders`'s response body), and `orderUpdates` / `userFills`
(the last 10 history rows under `data.rows`, in REST's long field names rather
than the compact wire ones). Dispatch handles the routing; your callback still
has to read both shapes.

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
