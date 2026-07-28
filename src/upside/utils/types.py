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

# Trigger price feeds for TP/SL: 0 = mark, 1 = index, 2 = last.
TriggerType = Literal[0, 1, 2]

# Position side for conditional orders / margin ops: 0 = ONE_WAY, 1 = LONG, 2 = SHORT.
PositionSide = Literal[0, 1, 2]


class OrderRequest(TypedDict):
    """A single order in a (possibly batched) ``order`` action.

    Prices and sizes are **raw integer strings** — scale them with the
    contract's ``priceScale`` / ``qtyScale`` from ``configs``. ``price`` is
    required for limit orders and omitted for market orders (``is_market``).
    """

    asset: int
    is_buy: bool
    size: str
    price: NotRequired[str]
    reduce_only: NotRequired[bool]
    tif: NotRequired[Tif]
    is_market: NotRequired[bool]
    cloid: NotRequired[str]
    builder_address: NotRequired[str]
    builder_fee: NotRequired[int]


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
    address. Only the fields relevant to ``type`` are read.
    """

    type: str
    asset: NotRequired[str]
    interval: NotRequired[str]
    user: NotRequired[str]


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
