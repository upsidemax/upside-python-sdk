"""Environment URLs and protocol constants for the UpsideMAX API.

Contract IDs, coin IDs, price/quantity scales, and tick/step sizes are **not**
constants — they are server-assigned and differ per environment. Always read
them from ``Info.configs()`` rather than hardcoding.
"""

# REST + WebSocket base URL of the UpsideMAX Devnet. The base URL is the scheme +
# host only; the SDK appends ``/info``, ``/exchange`` for REST and derives the
# ``/ws`` path. Pass a different ``base_url`` to Info/Exchange for other
# environments.
DEVNET_API_URL = "https://dev.upsidemax.xyz"

# EIP-712 signing domain (see https://docs.upsidemax.xyz/guide/authentication).
CHAIN_ID = 9767
DOMAIN_NAME = "Exchange"
DOMAIN_VERSION = "1"
VERIFYING_CONTRACT = "0x0000000000000000000000000000000000000000"
# Source string folded into the Agent-path struct.
AGENT_SOURCE = "b"

# Batch limits enforced by the gateway.
MAX_ORDERS_PER_REQUEST = 10
MAX_CANCELS_PER_REQUEST = 10

# Max orders in one userOrders / ordersByIds / ordersByCloids answer (and the
# openOrders snapshot). Past it the list is cut and ``truncated`` is true.
MAX_ORDERS_PER_QUERY = 850

# Max ids in one ordersByIds / ordersByCloids request; more is a 400.
MAX_QUERY_IDS = 256

# Max rows per page for the history queries (userFills / orderHistory /
# userFundingFlows); also their default. Asking for more is a 400.
MAX_HISTORY_LIMIT = 1000

# Trigger price feed for conditional orders and TP/SL. Only these two are
# accepted -- LAST (2) is rejected with "invalid tp/sl trigger type".
TRIGGER_TYPE_MARK = 0
TRIGGER_TYPE_ORACLE = 1

# Order type placed once a TP/SL leg triggers (tpOrderType / slOrderType).
# Required whenever the matching trigger price is set.
TPSL_ORDER_TYPE_LIMIT = 1
TPSL_ORDER_TYPE_MARKET = 2

# Direction of a trigger order / TP-SL leg (t.trigger.tpsl).
TPSL_TAKE_PROFIT = "tp"
TPSL_STOP_LOSS = "sl"

# Account margin sharing mode (setMarginShareType).
MARGIN_SHARE_UNIFIED = 0
MARGIN_SHARE_PORTFOLIO = 1

# Market-order slippage cap (updateSlippageSetting), in basis points. The
# server defaults to 1000 (10%) and accepts (0, 10000].
DEFAULT_MARKET_SLIPPAGE_BPS = 1000
MAX_MARKET_SLIPPAGE_BPS = 10000

# Candle intervals accepted by ``candleSnapshot`` and the ``candle`` WS channel.
INTERVALS = (
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "8h",
    "12h",
    "1d",
    "3d",
    "1w",
    "1M",
)

# Time-in-force values accepted by the ``order`` action.
TIF_GTC = "Gtc"
TIF_IOC = "Ioc"
TIF_ALO = "Alo"
TIF_FOK = "Fok"
TIFS = (TIF_GTC, TIF_IOC, TIF_ALO, TIF_FOK)

# Envelope ``sync`` flag: only these actions accept it. Every other action is
# always answered synchronously and rejects the flag (400 SYNC_NOT_APPLICABLE).
SYNC_ACTIONS = ("order", "cancel", "cancelByCloid", "cancelAll", "modify")

# ``modify`` only accepts resting policies -- IOC/FOK never rest, so changing an
# order into one is meaningless and rejected.
MODIFY_TIFS = (TIF_GTC, TIF_ALO)
