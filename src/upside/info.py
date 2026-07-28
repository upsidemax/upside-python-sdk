"""Read-only ``POST /info`` queries and the WebSocket subscription facade.

Every method wraps a single ``/info`` call and returns the raw parsed JSON —
see the per-query field references at https://docs.upsidemax.xyz/info/overview.
Numbers come back as **raw integer strings**; scale them with the contract's
``priceScale`` / ``qtyScale`` from :meth:`configs`.
"""

from typing import Any, Dict, List, Optional, Union

from .api import API
from .utils.types import Json, Subscription
from .websocket_manager import WebsocketManager, WsCallback


class Info(API):
    """Client for market, account, and order reads, plus realtime streams."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        skip_ws: bool = False,
    ) -> None:
        super().__init__(base_url, timeout)
        self.ws_manager: Optional[WebsocketManager] = None
        if not skip_ws:
            self.ws_manager = WebsocketManager(self.base_url)
            self.ws_manager.start()

    # -- market data ------------------------------------------------------
    def configs(self, market_deployer_id: int = 0) -> Dict[str, Any]:
        """All coin and contract configuration (ids, scales, tick/step, tiers).

        Cache this — it only changes when contracts are listed. ``priceScale`` /
        ``qtyScale`` / ``tickSize`` / ``stepSize`` here are the source of truth
        for scaling every other raw value. Pass ``0`` for all deployers.
        """
        return self._info({"type": "configs", "marketDeployerId": market_deployer_id})

    def l2_book(self, asset: Union[int, str]) -> Dict[str, Any]:
        """Full L2 order-book snapshot. Raises ``ServerError`` (503 ``NOT_READY``)
        before any book exists — retry with backoff."""
        return self._info({"type": "l2Book", "asset": str(asset)})

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
        """Historical OHLCV candles (ascending by ``t``); the last bar may be open."""
        query: Dict[str, Any] = {"type": "candleSnapshot", "asset": str(asset), "interval": interval}
        if start_time is not None:
            query["startTime"] = start_time
        if end_time is not None:
            query["endTime"] = end_time
        return self._info(query)

    def share_group_state(self, group_id: int = 0) -> Dict[str, Any]:
        """Portfolio share-group definitions (membership, settle coin, status)."""
        return self._info({"type": "shareGroupState", "groupId": group_id})

    # -- account ----------------------------------------------------------
    def user_account(self, account_id: Union[int, str], market_deployer_id: int) -> Dict[str, Any]:
        """Equity, collateral, margin availability, and positions. Pass
        ``market_deployer_id=0`` for an account-wide overview."""
        return self._info({"type": "userAccount", "accountId": str(account_id), "marketDeployerId": market_deployer_id})

    def user_market_deployers(self, account_id: Union[int, str]) -> Dict[str, Any]:
        """Market deployer ids the account is enrolled in."""
        return self._info({"type": "userMarketDeployers", "accountId": str(account_id)})

    def user_agents(self, account_id: Union[int, str]) -> Dict[str, Any]:
        """Authorized agent (API-wallet) slots for a master account."""
        return self._info({"type": "userAgents", "accountId": str(account_id)})

    # -- orders -----------------------------------------------------------
    def user_orders(
        self,
        account_id: Union[int, str],
        market_deployer_id: int,
        contract_id: int = 0,
    ) -> Dict[str, Any]:
        """Active open orders; ``contract_id=0`` returns all contracts."""
        return self._info(
            {
                "type": "userOrders",
                "accountId": str(account_id),
                "marketDeployerId": market_deployer_id,
                "contractId": contract_id,
            }
        )

    def orders_by_ids(self, market_deployer_id: int, order_ids: List[Union[int, str]]) -> Dict[str, Any]:
        """Look up orders by exchange order id (missing ids are omitted)."""
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
        """Look up orders by client order id (missing cloids are omitted)."""
        return self._info(
            {
                "type": "ordersByCloids",
                "accountId": str(account_id),
                "marketDeployerId": market_deployer_id,
                "cloids": [str(c) for c in cloids],
            }
        )

    # -- websocket facade -------------------------------------------------
    def subscribe(self, subscription: Subscription, callback: WsCallback) -> int:
        """Subscribe to a realtime channel; ``callback`` receives each push dict.

        Example: ``info.subscribe({"type": "l2Book", "asset": "1"}, print)``.
        Returns a subscription id for :meth:`unsubscribe`.
        """
        return self._require_ws().subscribe(subscription, callback)

    def unsubscribe(self, subscription: Subscription, subscription_id: int) -> bool:
        """Remove a previously registered subscription callback."""
        return self._require_ws().unsubscribe(subscription, subscription_id)

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
