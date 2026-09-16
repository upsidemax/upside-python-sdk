"""Environment URLs and protocol constants for the Upside API.

Contract IDs, coin IDs, price/quantity scales, and tick/step sizes are **not**
constants — they are server-assigned and differ per environment. Always read
them from ``Info.configs()`` rather than hardcoding.
"""

# REST + WebSocket base URL. The base URL is the scheme + host only; the SDK
# appends ``/info``, ``/exchange`` for REST and derives the ``/ws`` path. Pass a
# different ``base_url`` to Info/Exchange for other environments.
UAT_API_URL = "https://dev.upsidemax.xyz"

# Deprecated alias kept for callers written against the pre-rename name.
QA_API_URL = UAT_API_URL

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
