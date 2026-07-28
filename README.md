# Upside Python SDK

A Python client for the [Upside](https://docs.upsidemax.xyz) perpetuals
exchange — REST reads (`POST /info`), signed writes (`POST /exchange`),
and realtime WebSocket streams. Authentication is EIP-712 wallet-signed
(secp256k1) rather than API keys.

- **EIP-712 request signing** (secp256k1) with the Agent and Typed paths — no API keys.
- **Synchronous REST** over `requests`, **threaded WebSocket** over `websocket-client`.
- Raw-dict responses, `TypedDict` inputs, full type hints (ships `py.typed`).
- Agent (API-wallet) delegation, TP/SL, leverage/margin, and collateral actions.

> The default environment is the **QA testnet** (`https://dev.upsidemax.xyz`).
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
info = Info(base_url=constants.QA_API_URL)
cfg = info.configs()
contract = next(c for c in cfg["contracts"] if c["status"] == "Active")
asset = contract["contractId"]
print(info.market_state(asset))

# --- writes (EIP-712 signed) ---
exchange = Exchange("0x<private-key>", base_url=constants.QA_API_URL)

# Register (QA requires an invite code from the Upside team). A 10,000 USDC
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
info.user_agents(account_id)
info.user_market_deployers(account_id)
info.share_group_state()
```

## Trading — `Exchange`

```python
from upside import Cloid

exchange.order(asset=1, is_buy=True, size="10", price="50", cloid=Cloid.from_int(1001))
exchange.market_order(asset=1, is_buy=False, size="5")
exchange.bulk_orders([...])                     # up to 10 orders, one signature
exchange.cancel(asset=1, oid=15)
exchange.cancel_by_cloid(asset=1, cloid=1001)
exchange.cancel_all(asset=1)
exchange.modify(asset=1, oid=15, price="151", size="8")

exchange.update_leverage(asset=1, leverage=20)
exchange.update_margin_mode(asset=1, is_cross=False, is_hedge=True)
exchange.update_isolated_margin(asset=1, ntli=5000)

exchange.tp_sl(asset=1, tp_price="90000", sl_price="80000")
exchange.cancel_tp_sl(asset=1)
exchange.cancel_conditional(oid=123)

exchange.lock_collateral(market_deployer_id=1, coin_id=1, amount="1000")
exchange.transfer_between_deployers(1, 2, coin_id=1, amount="1000")
```

### Order placement is asynchronous

A batch returns `{"status": "accepted", "response": {"type": "order", "data": {"count": n}}}`
— **not** the resting order id. Read the resulting state from
`Info.user_orders` / `orders_by_cloids`, or the `orderUpdates` / `userFills`
WebSocket channels. Cancels, modifies, and margin actions respond synchronously.

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
agent = Exchange(agent_key, base_url=constants.QA_API_URL, account_id=master.account_id)
agent.order(asset=1, is_buy=True, size="10", price="50")
master.revoke_agent(agent.address)
```

## WebSocket streams

```python
info = Info(base_url=constants.QA_API_URL)          # WS starts automatically

sid = info.subscribe({"type": "l2Book", "asset": "1"}, lambda m: print(m["data"]["bookVersion"]))
info.subscribe({"type": "trades", "asset": "1"}, print)
info.subscribe({"type": "orderUpdates", "user": "0x<address>"}, print)   # private: pass the wallet address
info.subscribe({"type": "userFills", "user": "0x<address>"}, print)

info.unsubscribe({"type": "l2Book", "asset": "1"}, sid)
info.close()
```

Channels: `l2Book`, `bbo`, `trades`, `candle`, `config` (public) and
`orderUpdates`, `openOrders`, `userFills` (per-address). The client pings every
30s and auto-reconnects, replaying subscriptions. WebSocket does **not** push
position or balance changes — poll `userAccount` for those.

## Signing

Every `/exchange` write is authorized by an EIP-712 signature over a fixed
domain (`Exchange` / `1` / chainId `9767` / zero verifying contract). The SDK
handles both paths automatically:

- **Typed path** — `registerAccount`, `approveAgent`, `revokeAgent`,
  `lockCollateral`, `unlockCollateral`, `transferBetweenDeployers`.
- **Agent path** — every other action (canonical-JSON `actionHash`).

Nonces are strictly increasing millisecond timestamps managed per `Exchange`
instance (`NonceManager`). See
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
