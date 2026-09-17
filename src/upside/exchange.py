"""Signed ``POST /exchange`` actions: orders, cancels, margin, agents, collateral.

Each method builds the action dict, signs it (Agent or Typed EIP-712 path,
chosen automatically by action type), wraps it in the
``{action, signature, nonce}`` envelope, and POSTs it. Prices and sizes are
**raw integer strings** — scale them with the contract's ``priceScale`` /
``qtyScale`` from :meth:`Info.configs`.

Order placement is asynchronous: a batch returns ``{"status": "accepted",
"response": {"type": "order", "data": {"count": n}}}``. Observe resting/filled
state via :meth:`Info.user_orders` or the ``orderUpdates`` / ``userFills``
WebSocket channels. Cancels, modifies, and margin actions respond
synchronously. See https://docs.upsidemax.xyz/exchange/overview.
"""

import secrets
from typing import Any, Dict, List, Optional, Tuple, Union

from eth_account import Account

from .api import API
from .utils import constants
from .utils.signing import NonceManager, Signature, Wallet, sign_action, to_wallet
from .utils.types import (
    CancelByCloidRequest,
    CancelRequest,
    Cloid,
    Json,
    OrderRequest,
    Tif,
    as_cloid_str,
    cloid_str,
)


class Exchange(API):
    """Client for every state-changing operation, signed by ``wallet``.

    ``wallet`` may be a private-key hex string or an ``eth_account`` LocalAccount.
    For delegated trading, sign with an agent key here while ``account_id`` still
    refers to the master account (the server routes by recovered signer).
    """

    def __init__(
        self,
        wallet: Union[Wallet, str],
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        account_id: Optional[Union[int, str]] = None,
        nonce_manager: Optional[NonceManager] = None,
    ) -> None:
        super().__init__(base_url, timeout)
        self.wallet: Wallet = to_wallet(wallet)
        self.address: str = self.wallet.address.lower()
        self.account_id = str(account_id) if account_id is not None else None
        self.nonce_manager = nonce_manager or NonceManager()

    # ------------------------------------------------------------------ #
    # Account
    # ------------------------------------------------------------------ #
    def register_account(
        self,
        invite_code: Optional[str] = None,
        address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register the wallet and receive an ``accountId`` (typed path).

        ``invite_code`` is required in gated environments (UAT) and is sent
        unsigned at the envelope top level. On UAT a successful registration
        triggers a 10,000 USDC test airdrop within ~10s.
        """
        action = {"type": "registerAccount", "address": (address or self.address).lower()}
        extra = {"inviteCode": invite_code} if invite_code is not None else None
        result = self._post_action(action, extra)
        if isinstance(result, dict):
            account_id = result.get("response", {}).get("accountId")
            if account_id is not None:
                self.account_id = str(account_id)
        return self._as_dict(result)

    def enroll_user_to_market_deployer(self, market_deployer_id: int) -> Dict[str, Any]:
        """Enroll in an additional market deployer (idempotent)."""
        return self._post_dict({"type": "enrollUserToMarketDeployer", "marketDeployerId": market_deployer_id})

    def approve_agent(
        self,
        agent_address: Optional[str] = None,
        agent_name: str = "",
        valid_until: int = 0,
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """Authorize an agent wallet to sign trades (typed, master-only).

        If ``agent_address`` is omitted, a fresh key is generated; the returned
        tuple is ``(response, agent_private_key)`` so you can construct an agent
        :class:`Exchange`. When an address is supplied, the second element is
        ``None``. ``valid_until`` is Unix ms (``0`` = permanent).
        """
        agent_key: Optional[str] = None
        if agent_address is None:
            agent_key = "0x" + secrets.token_hex(32)
            agent_address = Account.from_key(agent_key).address.lower()
        action = {
            "type": "approveAgent",
            "agentAddress": agent_address.lower(),
            "agentName": agent_name,
            "validUntil": valid_until,
        }
        return self._post_dict(action), agent_key

    def revoke_agent(self, agent_address: str) -> Dict[str, Any]:
        """Revoke a previously approved agent (typed, master-only)."""
        return self._post_dict({"type": "revokeAgent", "agentAddress": agent_address.lower()})

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #
    def order(
        self,
        asset: int,
        is_buy: bool,
        size: Union[str, int],
        price: Optional[Union[str, int]] = None,
        reduce_only: bool = False,
        tif: Tif = "Gtc",
        cloid: Optional[Union[str, int, Cloid]] = None,
        is_market: bool = False,
        builder_address: Optional[str] = None,
        builder_fee: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Place a single limit or market order. See :meth:`bulk_orders`."""
        request: OrderRequest = {
            "asset": asset,
            "is_buy": is_buy,
            "size": str(size),
            "reduce_only": reduce_only,
            "is_market": is_market,
        }
        if price is None:
            raise ValueError(
                "price is required for every order. For a market order pass the "
                "execution price you are willing to cross to, derived from the mark "
                "price and your account's marketSlippageBps."
            )
        request["price"] = str(price)
        if not is_market:
            request["tif"] = tif
        if cloid is not None:
            request["cloid"] = cloid_str(cloid)
        if builder_address is not None:
            request["builder_address"] = builder_address
        if builder_fee is not None:
            request["builder_fee"] = builder_fee
        return self.bulk_orders([request])

    def market_order(
        self,
        asset: int,
        is_buy: bool,
        size: Union[str, int],
        price: Union[str, int],
        reduce_only: bool = False,
        cloid: Optional[Union[str, int, Cloid]] = None,
    ) -> Dict[str, Any]:
        """Place a market order, executed immediately as IOC.

        ``price`` is **required**: it is the execution price you are willing to
        cross to, not a resting price. Derive it from the mark price and your
        account's ``marketSlippageBps`` — the server does not compute one, and
        an order without a positive price is rejected.

        Market orders are not price-band checked, so a mistaken price can sweep
        the book. Validate it before calling.
        """
        return self.order(
            asset, is_buy, size, price=price, reduce_only=reduce_only, cloid=cloid, is_market=True
        )

    def bulk_orders(self, orders: List[OrderRequest]) -> Dict[str, Any]:
        """Place up to 10 orders in one signed request.

        In a batch, only ``orders[0]``'s builder fields apply to the whole batch.
        """
        if not 1 <= len(orders) <= constants.MAX_ORDERS_PER_REQUEST:
            raise ValueError(f"orders must contain 1..{constants.MAX_ORDERS_PER_REQUEST} items")
        action = {
            "type": "order",
            "orders": [_order_to_wire(o) for o in orders],
            "grouping": "na",
        }
        return self._post_dict(action)

    def cancel(self, asset: int, oid: int) -> Dict[str, Any]:
        """Cancel one resting order by exchange order id."""
        return self.bulk_cancel([{"asset": asset, "oid": oid}])

    def bulk_cancel(self, cancels: List[CancelRequest]) -> Dict[str, Any]:
        """Cancel up to 10 orders by order id in one request."""
        if not 1 <= len(cancels) <= constants.MAX_CANCELS_PER_REQUEST:
            raise ValueError(f"cancels must contain 1..{constants.MAX_CANCELS_PER_REQUEST} items")
        action = {"type": "cancel", "cancels": [{"a": c["asset"], "o": c["oid"]} for c in cancels]}
        return self._post_dict(action)

    def cancel_by_cloid(self, asset: int, cloid: Union[str, int, Cloid]) -> Dict[str, Any]:
        """Cancel one resting order by client order id."""
        return self.bulk_cancel_by_cloid([{"asset": asset, "cloid": cloid_str(cloid)}])

    def bulk_cancel_by_cloid(self, cancels: List[CancelByCloidRequest]) -> Dict[str, Any]:
        """Cancel up to 10 orders by client order id in one request."""
        if not 1 <= len(cancels) <= constants.MAX_CANCELS_PER_REQUEST:
            raise ValueError(f"cancels must contain 1..{constants.MAX_CANCELS_PER_REQUEST} items")
        action = {
            "type": "cancelByCloid",
            "cancels": [{"a": c["asset"], "cloid": cloid_str(c["cloid"])} for c in cancels],
        }
        return self._post_dict(action)

    def cancel_all(self, asset: int) -> Dict[str, Any]:
        """Cancel every open limit and conditional order for one contract."""
        return self._post_dict({"type": "cancelAll", "a": asset})

    def modify(
        self,
        asset: int,
        oid: Optional[int] = None,
        cloid: Optional[Union[str, int, Cloid]] = None,
        price: Optional[Union[str, int]] = None,
        size: Optional[Union[str, int]] = None,
        tif: Optional[Tif] = None,
        new_cloid: Optional[Union[str, int, Cloid]] = None,
    ) -> Dict[str, Any]:
        """Modify a resting order located by ``oid`` (preferred) or ``cloid``.

        Only price/size/TIF/cloid may change; side, asset, and reduce-only cannot.
        Provide only the fields you want to change.
        """
        if oid is None and cloid is None:
            raise ValueError("provide oid or cloid to locate the order")
        action: Dict[str, Any] = {"type": "modify", "a": asset}
        if oid is not None:
            action["oid"] = oid
        elif cloid is not None:
            action["cloid"] = as_cloid_str(cloid)
        if price is not None:
            action["p"] = str(price)
        if size is not None:
            action["s"] = str(size)
        if tif is not None:
            action["tif"] = tif
        if new_cloid is not None:
            action["c"] = as_cloid_str(new_cloid)
        return self._post_dict(action)

    # ------------------------------------------------------------------ #
    # Margin & leverage (full-name fields)
    # ------------------------------------------------------------------ #
    def update_leverage(self, asset: int, leverage: int) -> Dict[str, Any]:
        """Set the leverage multiplier for a contract."""
        return self._post_dict({"type": "updateLeverage", "a": asset, "leverage": leverage})

    def update_margin_mode(
        self,
        asset: int,
        is_cross: bool,
        is_hedge: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Switch a contract between cross (``True``) and isolated (``False``).

        Requires no open position or orders on the contract. Omit ``is_hedge`` to
        keep the current position mode; isolated requires HEDGE.
        """
        action: Dict[str, Any] = {"type": "updateMarginMode", "asset": asset, "isCross": is_cross}
        if is_hedge is not None:
            action["isHedge"] = is_hedge
        return self._post_dict(action)

    def update_isolated_margin(
        self,
        asset: int,
        ntli: int,
        is_buy: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Add (``ntli > 0``) or remove (``ntli < 0``) isolated margin.

        ``is_buy`` selects the side in HEDGE mode; omit it in ONE_WAY.
        """
        action: Dict[str, Any] = {"type": "updateIsolatedMargin", "asset": asset, "ntli": ntli}
        if is_buy is not None:
            action["isBuy"] = is_buy
        return self._post_dict(action)

    def tp_sl(
        self,
        asset: int,
        tp_price: Union[str, int] = "0",
        sl_price: Union[str, int] = "0",
        position_side: int = 0,
        is_position_tpsl: bool = True,
        order_side: Optional[str] = None,
        reduce_only: bool = False,
        tp_limit_price: Union[str, int] = "0",
        sl_limit_price: Union[str, int] = "0",
        tp_size: Union[str, int] = "0",
        sl_size: Union[str, int] = "0",
        tp_trigger_type: int = 0,
        sl_trigger_type: int = 0,
    ) -> Dict[str, Any]:
        """Attach take-profit and/or stop-loss triggers to a position.

        At least one of ``tp_price`` / ``sl_price`` must be ``> 0``. Limit price
        ``"0"`` fires a market IOC on trigger; ``> 0`` a GTC limit. Size ``"0"``
        closes the whole position. Trigger type: 0=mark, 1=index, 2=last.
        """
        action: Dict[str, Any] = {
            "type": "tpSl",
            "a": asset,
            "positionSide": position_side,
            "isPositionTpsl": is_position_tpsl,
            "reduceOnly": reduce_only,
            "tpPrice": str(tp_price),
            "slPrice": str(sl_price),
            "tpLimitPrice": str(tp_limit_price),
            "slLimitPrice": str(sl_limit_price),
            "tpSize": str(tp_size),
            "slSize": str(sl_size),
            "tpTriggerType": tp_trigger_type,
            "slTriggerType": sl_trigger_type,
        }
        if order_side is not None:
            action["orderSide"] = order_side
        return self._post_dict(action)

    def cancel_tp_sl(self, asset: int, position_side: int = 0) -> Dict[str, Any]:
        """Cancel all TP/SL trigger orders for a position (idempotent)."""
        return self._post_dict({"type": "cancelTpSl", "a": asset, "positionSide": position_side})

    def cancel_conditional(self, oid: int) -> Dict[str, Any]:
        """Cancel a single conditional order by id."""
        return self._post_dict({"type": "cancelConditional", "oid": oid})

    # ------------------------------------------------------------------ #
    # Collateral (typed path)
    # ------------------------------------------------------------------ #
    def lock_collateral(self, market_deployer_id: int, coin_id: int, amount: Union[str, int]) -> Dict[str, Any]:
        """Lock collateral into a market deployer's margin pool (typed path)."""
        return self._post_dict(
            {
                "type": "lockCollateral",
                "marketDeployerId": market_deployer_id,
                "coinId": coin_id,
                "amount": str(amount),
            }
        )

    def unlock_collateral(self, market_deployer_id: int, coin_id: int, amount: Union[str, int]) -> Dict[str, Any]:
        """Unlock collateral from a market deployer (typed path)."""
        return self._post_dict(
            {
                "type": "unlockCollateral",
                "marketDeployerId": market_deployer_id,
                "coinId": coin_id,
                "amount": str(amount),
            }
        )

    def transfer_between_deployers(
        self,
        from_market_deployer_id: int,
        to_market_deployer_id: int,
        coin_id: int,
        amount: Union[str, int],
    ) -> Dict[str, Any]:
        """Move collateral between two market deployers (typed path)."""
        return self._post_dict(
            {
                "type": "transferBetweenDeployers",
                "fromMarketDeployerId": from_market_deployer_id,
                "toMarketDeployerId": to_market_deployer_id,
                "coinId": coin_id,
                "amount": str(amount),
            }
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _post_action(self, action: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Json:
        nonce = self.nonce_manager.next()
        signature: Signature = sign_action(self.wallet, action, nonce)
        envelope: Dict[str, Any] = {"action": action, "signature": signature, "nonce": nonce}
        if extra:
            envelope.update(extra)
        return self.post("/exchange", envelope)

    def _post_dict(self, action: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._as_dict(self._post_action(action, extra))

    @staticmethod
    def _as_dict(result: Json) -> Dict[str, Any]:
        assert isinstance(result, dict)
        return result


def _order_to_wire(order: OrderRequest) -> Dict[str, Any]:
    """Convert a Pythonic :class:`OrderRequest` to the compact wire order object."""
    wire: Dict[str, Any] = {
        "a": order["asset"],
        "b": order["is_buy"],
        "s": str(order["size"]),
        "r": bool(order.get("reduce_only", False)),
    }
    if "price" not in order:
        raise ValueError(
            "price is required for every order, including market orders: the wire "
            "protocol has no server-side price derivation"
        )
    wire["p"] = str(order["price"])
    if order.get("is_market"):
        wire["t"] = {"market": {}}
    else:
        wire["t"] = {"limit": {"tif": order.get("tif", constants.TIF_GTC)}}
    cloid = as_cloid_str(order.get("cloid"))
    if cloid is not None:
        wire["c"] = cloid
    if order.get("builder_address") is not None:
        wire["builderAddress"] = order["builder_address"]
    if order.get("builder_fee") is not None:
        wire["builderFee"] = order["builder_fee"]
    return wire
