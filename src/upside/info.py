"""Read-only ``POST /info`` queries and the WebSocket subscription facade.

Every method wraps a single ``/info`` call and returns the raw parsed JSON —
see the per-query field references at https://docs.upsidemax.xyz/info/overview.
Numbers come back as **raw integer strings**; scale them with the contract's
``priceScale`` / ``qtyScale`` from :meth:`configs`.

Every query here is read-only, so a read timeout, a dropped connection, or a
transient 502 / 503 / 504 is retried with backoff (see :class:`~upside.api.API`).
"""

from typing import Any, Dict, List, Optional, Union, cast

from .api import API, DEFAULT_BACKOFF, DEFAULT_MAX_RETRIES, Timeout
from .utils import constants
from .utils.types import Json, Subscription
from .websocket_manager import WebsocketManager, WsCallback


class Info(API):
    """Client for market, account, and order reads, plus realtime streams.

    ``timeout`` / ``max_retries`` / ``backoff``: see :class:`~upside.api.API`.
    ``ws_error_callback`` receives WebSocket ``error`` frames (a refused
    subscription) and failed ``AuthResult`` frames; see
    :class:`~upside.websocket_manager.WebsocketManager`.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[Timeout] = None,
        skip_ws: bool = False,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff: float = DEFAULT_BACKOFF,
        ws_error_callback: Optional[WsCallback] = None,
    ) -> None:
        super().__init__(base_url, timeout, max_retries, backoff)
        self._tick_sizes: Optional[Dict[str, int]] = None
        self.ws_manager: Optional[WebsocketManager] = None
        if not skip_ws:
            self.ws_manager = WebsocketManager(self.base_url, error_callback=ws_error_callback)
            self.ws_manager.start()

    # -- market data ------------------------------------------------------
    def configs(self, market_deployer_id: int = 0) -> Dict[str, Any]:
        """All coin and contract configuration (ids, scales, tick/step, tiers).

        Cache this — it only changes when contracts are listed. ``priceScale`` /
        ``qtyScale`` / ``tickSize`` / ``stepSize`` here are the source of truth
        for scaling every other raw value. Pass ``0`` for all deployers.
        """
        return self._info({"type": "configs", "marketDeployerId": market_deployer_id})

    def l2_book(self, asset: Union[int, str], group: Optional[Union[int, str]] = None) -> Dict[str, Any]:
        """Full L2 order-book snapshot.

        ``group`` selects a price grouping — one of the contract's ``configs``
        ``digitMerge`` values; omit it for the raw book. A grouping the contract
        does not have is ``400 BAD_REQUEST``. Raises ``ServerError``
        (503 ``NOT_READY``) when there is no book for ``asset``: not yet built,
        no longer mirrored, or no such contract — retrying only helps the first.
        """
        query: Dict[str, Any] = {"type": "l2Book", "asset": str(asset)}
        if group is not None:
            query["group"] = int(group)
        return self._info(query)

    def market_state(self, asset: Union[int, str]) -> Dict[str, Any]:
        """Mark / oracle / last price, ``priceReady`` flag, and funding index."""
        return self._info({"type": "marketState", "asset": str(asset)})

    def candle_snapshot(
        self,
        asset: Union[int, str],
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Historical OHLCV candles (ascending by ``t``); the last bar may be open.

        Pass a time window (Unix ms). Without one the server answers with its
        full retained history for the asset, which on an active contract can
        exceed the reply size limit.
        """
        if interval not in constants.INTERVALS:
            raise ValueError(f"interval must be one of {', '.join(constants.INTERVALS)}, got {interval!r}")
        query: Dict[str, Any] = {"type": "candleSnapshot", "asset": str(asset), "interval": interval}
        if start_time is not None:
            query["startTime"] = start_time
        if end_time is not None:
            query["endTime"] = end_time
        return self._info(query)

    def ticker(self, asset: Optional[Union[int, str]] = None) -> Dict[str, Any]:
        """24h rolling stats: change, high/low, volume, funding, mark/oracle.

        Omit ``asset`` for every contract that has traded. The window slides
        over ``[now - 24h, now]``; it is not a calendar day.
        """
        query: Dict[str, Any] = {"type": "ticker"}
        if asset is not None:
            query["asset"] = str(asset)
        return self._info(query)

    def share_group_state(self, group_id: int = 0) -> Dict[str, Any]:
        """Portfolio share-group definitions (membership, settle coin, status)."""
        return self._info({"type": "shareGroupState", "groupId": group_id})

    # -- account ----------------------------------------------------------
    def user_account(self, account_id: Union[int, str], market_deployer_id: int) -> Dict[str, Any]:
        """Equity, collateral, margin availability, and positions.

        ``market_deployer_id=0`` returns the account-wide fields (chain-level
        balances, the deployers it is enrolled in); per-deployer figures such as
        ``crossEquity`` then read ``"0"`` — query each deployer for those.
        """
        return self._info({"type": "userAccount", "accountId": str(account_id), "marketDeployerId": market_deployer_id})

    def user_market_deployers(self, account_id: Union[int, str]) -> Dict[str, Any]:
        """Market deployer ids the account is enrolled in."""
        return self._info({"type": "userMarketDeployers", "accountId": str(account_id)})

    def user_agents(self, account_id: Union[int, str]) -> Dict[str, Any]:
        """Authorized agent (API-wallet) slots for a master account."""
        return self._info({"type": "userAgents", "accountId": str(account_id)})

    def account_by_address(self, address: str) -> Dict[str, Any]:
        """Resolve a wallet address to an ``accountId``.

        Works for a master address and for an agent address (which resolves to
        the master it is bound to, with ``isAgent: true``). An unregistered
        address comes back as ``accountId: "0"`` rather than an error.
        """
        return self._info({"type": "accountByAddress", "address": address.lower()})

    # -- orders -----------------------------------------------------------
    def user_orders(
        self,
        account_id: Union[int, str],
        market_deployer_id: int = 0,
        contract_id: int = 0,
    ) -> Dict[str, Any]:
        """Active open orders, plus conditional orders not yet triggered.

        ``market_deployer_id=0`` covers every deployer the account is enrolled
        in; ``contract_id=0`` every contract. At most
        ``constants.MAX_ORDERS_PER_QUERY`` orders come back: when ``truncated``
        is true the list is incomplete, so query deployer by deployer rather
        than treating a missing order as gone.

        Field names differ from :meth:`order_history`: ``id`` / ``originalSize``
        / ``size`` (remaining) / ``timestamp`` here are ``orderId`` / ``origQty``
        / ``leavesQty`` / ``createdTimeMs`` there. ``nonce`` here is an
        account-level command sequence, not your signing nonce — correlate by
        ``cloid``.
        """
        return self._info(
            {
                "type": "userOrders",
                "accountId": str(account_id),
                "marketDeployerId": market_deployer_id,
                "contractId": contract_id,
            }
        )

    def orders_by_ids(self, market_deployer_id: int, order_ids: List[Union[int, str]]) -> Dict[str, Any]:
        """Look up **live** orders (resting, partially filled) by exchange order id.

        Filled, canceled, and not-yet-triggered conditional orders are omitted,
        as are ids from another deployer — use :meth:`order_history` for
        finished orders. ``market_deployer_id`` must be the order's deployer
        (``0`` would match nothing). At most ``constants.MAX_QUERY_IDS`` ids per
        call. Same order fields and ``truncated`` flag as :meth:`user_orders`.
        """
        _check_id_list("order_ids", order_ids, market_deployer_id)
        return self._info(
            {
                "type": "ordersByIds",
                "marketDeployerId": market_deployer_id,
                "orderIds": [str(o) for o in order_ids],
            }
        )

    def orders_by_cloids(
        self,
        account_id: Union[int, str],
        market_deployer_id: int,
        cloids: List[Union[int, str]],
    ) -> Dict[str, Any]:
        """Look up **live** orders (resting, partially filled) by client order id.

        Same scope and limits as :meth:`orders_by_ids`: finished orders are not
        here — use :meth:`order_history`.
        """
        _check_id_list("cloids", cloids, market_deployer_id)
        return self._info(
            {
                "type": "ordersByCloids",
                "accountId": str(account_id),
                "marketDeployerId": market_deployer_id,
                "cloids": [str(c) for c in cloids],
            }
        )

    # -- history ----------------------------------------------------------
    def user_fills(
        self,
        account_id: Union[int, str],
        contract_id: int = 0,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Historical fills, one row per side the account was on.

        Rows are ascending by ``timeMs``; page by passing the last row's
        ``timeMs`` as the next ``start_time`` and de-duplicating on ``execId``
        (the bound is inclusive). There may be more rows when ``count == limit``
        **or** when ``truncated`` is true (the page was cut short to fit the
        reply, so ``count < limit``). If a full page all shares one ``timeMs``
        the next page repeats it — raise ``limit`` (up to
        ``constants.MAX_HISTORY_LIMIT``). ``end_time=0`` means no upper bound.
        """
        return self._info(self._history_query("userFills", account_id, contract_id, start_time, end_time, limit))

    def order_history(
        self,
        account_id: Union[int, str],
        contract_id: int = 0,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Terminal orders (filled / canceled / rejected / triggered).

        Resting and partially filled orders are **not** here — use
        :meth:`user_orders`. Pagination matches :meth:`user_fills`, keyed on
        ``updatedTimeMs`` / ``orderId``.
        """
        return self._info(self._history_query("orderHistory", account_id, contract_id, start_time, end_time, limit))

    def user_funding_flows(
        self,
        account_id: Union[int, str],
        contract_id: int = 0,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Per-position funding settlements. Pagination matches :meth:`user_fills`.

        ``amount`` is signed (negative = paid). Reconcile across periods with
        ``fundingIndexBefore`` / ``fundingIndexAfter``, not with ``fundingRate``
        — the rate is only the most recent period's.
        """
        return self._info(self._history_query("userFundingFlows", account_id, contract_id, start_time, end_time, limit))

    @staticmethod
    def _history_query(
        query_type: str,
        account_id: Union[int, str],
        contract_id: int,
        start_time: Optional[int],
        end_time: Optional[int],
        limit: Optional[int],
    ) -> Dict[str, Any]:
        """Shared envelope for the three paginated history queries."""
        if limit is not None and not 1 <= limit <= constants.MAX_HISTORY_LIMIT:
            raise ValueError(f"limit must be 1..{constants.MAX_HISTORY_LIMIT}")
        # 0 is "unbounded" on either side, so only two real bounds can conflict.
        if start_time and end_time and start_time > end_time:
            raise ValueError("start_time must not be after end_time")
        query: Dict[str, Any] = {"type": query_type, "accountId": str(account_id), "contractId": contract_id}
        if start_time is not None:
            query["startTime"] = start_time
        if end_time is not None:
            query["endTime"] = end_time
        if limit is not None:
            query["limit"] = limit
        return query

    # -- websocket facade -------------------------------------------------
    def subscribe(self, subscription: Subscription, callback: WsCallback) -> int:
        """Subscribe to a realtime channel; ``callback`` receives each push dict.

        Example: ``info.subscribe({"type": "l2Book", "asset": "1"}, print)``.
        Returns a subscription id for :meth:`unsubscribe`. An ``l2Book``
        ``group`` equal to the contract's ``tickSize`` is the raw book, and is
        subscribed as such (the server streams it on the raw channel).
        """
        return self._require_ws().subscribe(self._normalize_l2book_group(subscription), callback)

    def unsubscribe(self, subscription: Subscription, subscription_id: int) -> bool:
        """Remove a previously registered subscription callback."""
        return self._require_ws().unsubscribe(self._normalize_l2book_group(subscription), subscription_id)

    def _normalize_l2book_group(self, subscription: Subscription) -> Subscription:
        if subscription.get("type") != "l2Book" or not subscription.get("group"):
            return subscription
        tick = self._tick_size(str(subscription.get("asset")))
        if tick is None or int(subscription["group"]) != tick:
            return subscription
        raw = cast(Subscription, {k: v for k, v in subscription.items() if k != "group"})
        return raw

    def _tick_size(self, asset: str) -> Optional[int]:
        """``tickSize`` of ``asset`` from a cached ``configs`` (refetched once on a miss)."""
        for refresh in (False, True):
            if refresh or self._tick_sizes is None:
                contracts = self.configs().get("contracts") or []
                self._tick_sizes = {str(c.get("contractId")): _raw_int(c.get("tickSize")) for c in contracts}
            if asset in self._tick_sizes:
                return self._tick_sizes[asset] or None
        return None

    def ws_authenticate(self, account_id: int) -> None:
        """Send the optional WebSocket ``Auth`` frame for the connection."""
        self._require_ws().authenticate(account_id)

    # -- internals --------------------------------------------------------
    def _info(self, query: Dict[str, Any]) -> Dict[str, Any]:
        result: Json = self.post("/info", query)
        assert isinstance(result, dict)  # /info queries here return objects
        return result

    def _require_ws(self) -> WebsocketManager:
        if self.ws_manager is None:
            raise RuntimeError("WebSocket disabled — construct Info(skip_ws=False)")
        return self.ws_manager

    def close(self) -> None:
        if self.ws_manager is not None:
            self.ws_manager.stop()
        super().close()


def _check_id_list(name: str, ids: List[Union[int, str]], market_deployer_id: int) -> None:
    if not 1 <= len(ids) <= constants.MAX_QUERY_IDS:
        raise ValueError(f"{name} must hold 1..{constants.MAX_QUERY_IDS} ids")
    if market_deployer_id <= 0:
        raise ValueError("market_deployer_id must be the orders' deployer (> 0); 0 matches nothing here")


def _raw_int(value: Any) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0
