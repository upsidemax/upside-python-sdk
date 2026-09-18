"""Shared types for the Upside SDK.

The SDK keeps two vocabularies:

* the **request** side (Pythonic, ``snake_case``) used by public method
  signatures and the :class:`OrderRequest` / :class:`ModifyRequest` TypedDicts, and
* the **wire** side (compact single-letter keys, string numbers) that goes into
  the signed ``action`` payload.

Conversion between the two happens in :mod:`upside.exchange`. Responses are
returned as raw parsed JSON (``dict`` / ``list``) — inspect them by key.
"""

import sys
from typing import Any, Dict, List, Literal, Optional, Union

if sys.version_info >= (3, 11):
    from typing import NotRequired, TypedDict
else:  # ``NotRequired`` was added to ``typing`` in 3.11
    from typing_extensions import NotRequired, TypedDict

# Raw JSON returned by ``/info`` and ``/exchange``.
Json = Union[Dict[str, Any], List[Any]]

# Time-in-force policies accepted by the ``order`` action.
Tif = Literal["Gtc", "Ioc", "Alo", "Fok"]

# Trigger price feed for conditional orders: 0 = mark, 1 = oracle (a.k.a.
# index). LAST is not accepted -- the server rejects anything else.
TriggerType = Literal[0, 1]

# Order placed when a TP/SL leg triggers: 1 = limit (GTC), 2 = market (IOC).
# Required whenever the matching trigger price is set.
TpSlOrderType = Literal[1, 2]

# Direction of a trigger order / TP-SL leg.
TpSlSide = Literal["tp", "sl"]

# Position side for conditional orders / margin ops: 0 = ONE_WAY, 1 = LONG, 2 = SHORT.
PositionSide = Literal[0, 1, 2]

# Account margin sharing mode: 0 = UNIFIED (per-deployer), 1 = PORTFOLIO (shared).
MarginShareType = Literal[0, 1]


class OrderRequest(TypedDict):
    """A single order in a (possibly batched) ``order`` action.

    Prices and sizes are **raw integer strings** — scale them with the
    contract's ``priceScale`` / ``qtyScale`` from ``configs``. ``price`` is
    **required for every order**: for a limit order it is the resting price, for
    a market order (``is_market``) the execution price to cross to, and for a
    trigger order (``trigger_px``) the price of the order placed on trigger.

    Three mutually exclusive order types, selected by which fields are set:
    ``is_market`` (market IOC), ``trigger_px`` (conditional), or neither
    (limit with ``tif``). The ``tp_*`` / ``sl_*`` fields attach an entry-inline
    take-profit / stop-loss to a limit or market order; both they and trigger
    orders are **single-order only** — the server ignores or rejects them in a
    batch of two or more.
    """

    asset: int
    is_buy: bool
    size: str
    price: str
    reduce_only: NotRequired[bool]
    tif: NotRequired[Tif]
    is_market: NotRequired[bool]
    cloid: NotRequired[str]
    builder_address: NotRequired[str]
    builder_fee: NotRequired[int]
    # Trigger (conditional) order: fires a market (``trigger_is_market``) or
    # limit order once the mark price crosses ``trigger_px``.
    trigger_px: NotRequired[str]
    trigger_is_market: NotRequired[bool]
    trigger_tpsl: NotRequired[TpSlSide]
    # Entry-inline take-profit / stop-loss, promoted to position TP/SL once the
    # entry fills completely.
    tp_price: NotRequired[str]
    tp_limit_price: NotRequired[str]
    tp_size: NotRequired[str]
    tp_trigger_type: NotRequired[TriggerType]
    tp_order_type: NotRequired[TpSlOrderType]
    sl_price: NotRequired[str]
    sl_limit_price: NotRequired[str]
    sl_size: NotRequired[str]
    sl_trigger_type: NotRequired[TriggerType]
    sl_order_type: NotRequired[TpSlOrderType]


class CancelRequest(TypedDict):
    """A single cancel-by-order-id entry."""

    asset: int
    oid: int


class CancelByCloidRequest(TypedDict):
    """A single cancel-by-client-order-id entry."""

    asset: int
    cloid: str


class Subscription(TypedDict):
    """A WebSocket subscription object (the ``subscription`` field on the wire).

    ``asset`` is a decimal string contract id; ``user`` is a lowercase wallet
    address. ``marketDeployerId`` is required by ``userAccount`` only. Only the
    fields relevant to ``type`` are read.
    """

    type: str
    asset: NotRequired[str]
    interval: NotRequired[str]
    user: NotRequired[str]
    marketDeployerId: NotRequired[int]


class Cloid:
    """A client order id: a positive ``int64`` sent as a decimal string.

    Upside represents client order ids as ``int64`` decimal strings (unlike
    Hyperliquid's 128-bit hex cloids). Use this wrapper to build one from an
    int, a timestamp, or a validated string.
    """

    __slots__ = ("_value",)

    _MAX = 2**63 - 1

    def __init__(self, raw: str) -> None:
        value = int(raw)
        if value <= 0 or value > self._MAX:
            raise ValueError(f"cloid must be a positive int64 decimal string, got {raw!r}")
        self._value = value

    @classmethod
    def from_int(cls, value: int) -> "Cloid":
        return cls(str(value))

    def to_raw(self) -> str:
        """The wire representation: a decimal string."""
        return str(self._value)

    def to_int(self) -> int:
        return self._value

    def __str__(self) -> str:
        return self.to_raw()

    def __repr__(self) -> str:
        return f"Cloid({self.to_raw()!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Cloid) and other._value == self._value

    def __hash__(self) -> int:
        return hash(self._value)


def cloid_str(value: Union[str, int, Cloid]) -> str:
    """Normalize a required cloid-like value to its wire decimal string."""
    if isinstance(value, Cloid):
        return value.to_raw()
    return Cloid(str(value)).to_raw()


def as_cloid_str(value: Optional[Union[str, int, Cloid]]) -> Optional[str]:
    """Normalize an optional cloid-like value to its wire decimal string (or ``None``)."""
    return None if value is None else cloid_str(value)
