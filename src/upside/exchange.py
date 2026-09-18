"""Signed ``POST /exchange`` actions: orders, cancels, margin, agents, collateral.

Each method builds the action dict, signs it (Agent or Typed EIP-712 path,
chosen automatically by action type), wraps it in the
``{action, signature, nonce}`` envelope, and POSTs it. Prices and sizes are
**raw integer strings** — scale them with the contract's ``priceScale`` /
``qtyScale`` from :meth:`Info.configs`.

The order and cancel family (``order`` / ``cancel`` / ``cancelByCloid`` /
``cancelAll`` / ``modify``) answers with **either** HTTP 200 carrying a
``statuses[]`` entry per submitted item, **or** HTTP 202 carrying
``{"status": "accepted", "response": {"type": "accepted", "data": {"count": n}}}``
— ``type`` is the literal ``"accepted"``, not the action name. Handle both: on
202 the per-item outcome arrives on the ``orderUpdates`` WebSocket channel,
correlated by ``cloid`` or by ``n`` (your nonce) + ``si`` (index in the batch).
Every other action responds synchronously with ``status: "ok"`` and an
``errorCode`` inside ``response.data`` when rejected.
See https://docs.upsidemax.xyz/exchange/overview.
"""

import secrets
from typing import Any, Dict, List, Optional, Tuple, Union, cast

from eth_account import Account

from .api import API
from .utils import constants
from .utils.signing import NonceManager, Signature, Wallet, is_typed_action, sign_action, to_wallet
from .utils.types import (
    CancelByCloidRequest,
    CancelRequest,
    Cloid,
    Json,
    MarginShareType,
    OrderRequest,
    PositionSide,
    Tif,
    TpSlOrderType,
    TpSlSide,
    TriggerType,
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
        signature_chain_id: Optional[int] = None,
    ) -> None:
        super().__init__(base_url, timeout)
        self.wallet: Wallet = to_wallet(wallet)
        self.address: str = self.wallet.address.lower()
        self.account_id = str(account_id) if account_id is not None else None
        self.nonce_manager = nonce_manager or NonceManager()
        # Only browser-extension wallets need this: they sign typed structs with
        # their active chain id instead of 9767. With a raw key (what this SDK
        # signs with) leave it unset. Agent-path actions ignore it either way.
        self.signature_chain_id = signature_chain_id

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
        tp_price: Optional[Union[str, int]] = None,
        tp_limit_price: Optional[Union[str, int]] = None,
        tp_size: Optional[Union[str, int]] = None,
        tp_trigger_type: TriggerType = 0,
        tp_order_type: Optional[TpSlOrderType] = None,
        sl_price: Optional[Union[str, int]] = None,
        sl_limit_price: Optional[Union[str, int]] = None,
        sl_size: Optional[Union[str, int]] = None,
        sl_trigger_type: TriggerType = 0,
        sl_order_type: Optional[TpSlOrderType] = None,
    ) -> Dict[str, Any]:
        """Place a single limit or market order, optionally with inline TP/SL.

        ``tp_price`` / ``sl_price`` attach an entry-inline take-profit / stop-loss:
        once this order fills **completely** each set leg becomes a position TP/SL
        (a partial fill promotes nothing). A leg needs its ``*_limit_price`` (the
        price of the order placed on trigger) and its ``*_order_type``
        (1 = limit, 2 = market). Inline TP/SL cannot ride on a reduce-only order.

        For a conditional (trigger) order see :meth:`trigger_order`;
        for a batch see :meth:`bulk_orders`.
        """
        if price is None:
            raise ValueError(
                "price is required for every order. For a market order pass the "
                "execution price you are willing to cross to, derived from the mark "
                "price and your account's marketSlippageBps."
            )
        request: OrderRequest = {
            "asset": asset,
            "is_buy": is_buy,
            "size": str(size),
            "price": str(price),
            "reduce_only": reduce_only,
            "is_market": is_market,
        }
        if not is_market:
            request["tif"] = tif
        if cloid is not None:
            request["cloid"] = cloid_str(cloid)
        if builder_address is not None:
            request["builder_address"] = builder_address
        if builder_fee is not None:
            request["builder_fee"] = builder_fee
        _put_tpsl_leg(request, "tp", tp_price, tp_limit_price, tp_size, tp_trigger_type, tp_order_type)
        _put_tpsl_leg(request, "sl", sl_price, sl_limit_price, sl_size, sl_trigger_type, sl_order_type)
        return self.bulk_orders([request])

    def trigger_order(
        self,
        asset: int,
        is_buy: bool,
        size: Union[str, int],
        price: Union[str, int],
        trigger_px: Union[str, int],
        tpsl: TpSlSide,
        is_market: bool = True,
        reduce_only: bool = False,
        cloid: Optional[Union[str, int, Cloid]] = None,
    ) -> Dict[str, Any]:
        """Place a conditional order that fires when the mark price crosses ``trigger_px``.

        ``tpsl`` picks the comparison direction (``"tp"`` take-profit, ``"sl"``
        stop-loss). ``is_market`` chooses what is then placed: a market IOC
        (``True``) or a resting GTC limit (``False``) — either way ``price`` is
        required, as the execution price to cross to or the resting price.
        ``reduce_only=True`` makes it a closing order.

        Trigger orders must be sent alone (a batch containing one is rejected)
        and answer with the TP/SL receipt shape
        ``{"response": {"type": "tpSl", "data": {"tpOrderId": n, "slOrderId": n}}}``
        — the id lands in the leg matching ``tpsl``, the other is ``0``. Cancel
        one with :meth:`cancel_conditional`.
        """
        request: OrderRequest = {
            "asset": asset,
            "is_buy": is_buy,
            "size": str(size),
            "price": str(price),
            "reduce_only": reduce_only,
            "trigger_px": str(trigger_px),
            "trigger_is_market": is_market,
            "trigger_tpsl": tpsl,
        }
        if cloid is not None:
            request["cloid"] = cloid_str(cloid)
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
        return self.order(asset, is_buy, size, price=price, reduce_only=reduce_only, cloid=cloid, is_market=True)

    def bulk_orders(self, orders: List[OrderRequest]) -> Dict[str, Any]:
        """Place up to 10 orders in one signed request.

        In a batch, only ``orders[0]``'s builder fields apply to the whole batch.
        """
        if not 1 <= len(orders) <= constants.MAX_ORDERS_PER_REQUEST:
            raise ValueError(f"orders must contain 1..{constants.MAX_ORDERS_PER_REQUEST} items")
        if len(orders) > 1:
            # The server rejects a batch containing a trigger order outright and
            # silently drops inline TP/SL from a batch -- fail here instead.
            for index, order in enumerate(orders):
                if "trigger_px" in order:
                    raise ValueError(f"orders[{index}]: a trigger order must be sent on its own")
                fields = cast(Dict[str, Any], order)
                # A zeroed leg is the "not set" convention, not a dropped TP/SL.
                if any(_is_set(fields.get(f"{leg}_price")) for leg in ("tp", "sl")):
                    raise ValueError(f"orders[{index}]: inline TP/SL is ignored in a batch; send the order alone")
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
        if tif is not None and tif not in constants.MODIFY_TIFS:
            raise ValueError(f"modify accepts a resting tif only: {' / '.join(constants.MODIFY_TIFS)}")
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

        Requires no open position and no open orders on the contract. Omit
        ``is_hedge`` to keep the current position mode. **HEDGE is disabled** on
        the server right now — ``is_hedge=True`` is rejected — while isolated
        margin works under ONE_WAY.
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

    def update_slippage_setting(self, market_deployer_id: int, market_slippage_bps: int) -> Dict[str, Any]:
        """Set the account's market-order slippage cap, in basis points.

        The cap is per ``(account, market deployer)`` — not per contract — and
        defaults to 1000 (10%). Accepted range is ``(0, 10000]``. It is what you
        derive a market order's price from (``mark price ± bps/1e4``); the
        current value reads back as ``marketSlippageBps`` on
        :meth:`Info.user_account`.
        """
        if not 0 < market_slippage_bps <= constants.MAX_MARKET_SLIPPAGE_BPS:
            raise ValueError(f"market_slippage_bps must be in (0, {constants.MAX_MARKET_SLIPPAGE_BPS}]")
        return self._post_dict(
            {
                "type": "updateSlippageSetting",
                "marketDeployerId": market_deployer_id,
                "marketSlippageBps": market_slippage_bps,
            }
        )

    def set_margin_share_type(self, margin_share_type: MarginShareType) -> Dict[str, Any]:
        """Switch the account between UNIFIED (``0``) and PORTFOLIO (``1``) margin.

        UNIFIED keeps each market deployer's margin isolated; PORTFOLIO pools
        margin across the contracts of a share group. The account must have no
        position and no open order. An invalid value comes back as HTTP 200 with
        a non-zero ``data.errorCode``, not a 400.
        """
        return self._post_dict({"type": "setMarginShareType", "marginShareType": margin_share_type})

    def tp_sl(
        self,
        asset: int,
        tp_price: Union[str, int] = "0",
        sl_price: Union[str, int] = "0",
        position_side: PositionSide = 0,
        is_position_tpsl: bool = True,
        order_side: Optional[str] = None,
        reduce_only: bool = False,
        tp_limit_price: Union[str, int] = "0",
        sl_limit_price: Union[str, int] = "0",
        tp_size: Union[str, int] = "0",
        sl_size: Union[str, int] = "0",
        tp_trigger_type: TriggerType = 0,
        sl_trigger_type: TriggerType = 0,
        tp_order_type: Optional[TpSlOrderType] = None,
        sl_order_type: Optional[TpSlOrderType] = None,
    ) -> Dict[str, Any]:
        """Attach take-profit and/or stop-loss triggers to a position.

        At least one of ``tp_price`` / ``sl_price`` must be ``> 0``. Each leg you
        set needs its ``*_order_type`` (1 = limit, 2 = market) and its
        ``*_limit_price`` (``> 0``): the price of the order placed on trigger —
        a resting price for a limit leg, the price to cross to for a market one.
        Size ``"0"`` closes the whole position. Trigger type: 0 = mark,
        1 = oracle; LAST is not accepted.

        ``is_position_tpsl=True`` binds the legs to the position and derives the
        closing side; pass ``False`` with ``order_side`` (``"B"`` / ``"S"``) for
        a standalone trigger. The receipt carries ``tpOrderId`` / ``slOrderId``,
        ``0`` for a leg that was not set.
        """
        _check_tpsl_leg("tp", tp_price, tp_limit_price, tp_order_type)
        _check_tpsl_leg("sl", sl_price, sl_limit_price, sl_order_type)
        if not (_is_set(tp_price) or _is_set(sl_price)):
            raise ValueError("set tp_price or sl_price (or both) above 0")
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
            "tpOrderType": tp_order_type or 0,
            "slOrderType": sl_order_type or 0,
        }
        if order_side is not None:
            action["orderSide"] = order_side
        return self._post_dict(action)

    def cancel_tp_sl(self, asset: int, position_side: PositionSide = 0) -> Dict[str, Any]:
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
    # Shared (portfolio) margin group transfers
    # ------------------------------------------------------------------ #
    def transfer_md_to_share_group(
        self,
        market_deployer_id: int,
        group_id: int,
        coin_id: int,
        amount: Union[str, int],
    ) -> Dict[str, Any]:
        """Move a market deployer's cross margin into a shared margin group."""
        return self._post_dict(
            {
                "type": "transferMdToShareGroup",
                "marketDeployerId": market_deployer_id,
                "groupId": group_id,
                "coinId": coin_id,
                "amount": str(amount),
            }
        )

    def transfer_share_group_to_md(
        self,
        group_id: int,
        market_deployer_id: int,
        coin_id: int,
        amount: Union[str, int],
    ) -> Dict[str, Any]:
        """Move shared-group margin back into a market deployer's cross margin."""
        return self._post_dict(
            {
                "type": "transferShareGroupToMd",
                "groupId": group_id,
                "marketDeployerId": market_deployer_id,
                "coinId": coin_id,
                "amount": str(amount),
            }
        )

    def lock_into_share_group(self, group_id: int, coin_id: int, amount: Union[str, int]) -> Dict[str, Any]:
        """Move chain-level balance into a shared margin group."""
        return self._post_dict(
            {
                "type": "lockIntoShareGroup",
                "groupId": group_id,
                "coinId": coin_id,
                "amount": str(amount),
            }
        )

    def unlock_from_share_group(self, group_id: int, coin_id: int, amount: Union[str, int]) -> Dict[str, Any]:
        """Move shared-group margin back to chain-level balance.

        Capped by the group's withdrawable amount (margin in use is not
        withdrawable, and unrealized profit does not count).
        """
        return self._post_dict(
            {
                "type": "unlockFromShareGroup",
                "groupId": group_id,
                "coinId": coin_id,
                "amount": str(amount),
            }
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _post_action(self, action: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Json:
        nonce = self.nonce_manager.next()
        signature: Signature = sign_action(self.wallet, action, nonce, self.signature_chain_id)
        envelope: Dict[str, Any] = {"action": action, "signature": signature, "nonce": nonce}
        if self.signature_chain_id is not None and is_typed_action(action["type"]):
            # Top level, unsigned: the server rebuilds the typed domain with it.
            # Agent-path actions are always signed over 9767, so sending it there
            # would advertise a chain the signature does not use.
            envelope["signatureChainId"] = hex(self.signature_chain_id)
        if extra:
            envelope.update(extra)
        return self.post("/exchange", envelope)

    def _post_dict(self, action: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._as_dict(self._post_action(action, extra))

    @staticmethod
    def _as_dict(result: Json) -> Dict[str, Any]:
        assert isinstance(result, dict)
        return result


def _is_set(price: Optional[Union[str, int]]) -> bool:
    """A price field counts as set only when present and above zero."""
    if price is None:
        return False
    try:
        return int(str(price)) > 0
    except ValueError as exc:
        raise ValueError(f"price must be a raw integer string, got {price!r}") from exc


def _check_tpsl_leg(
    leg: str,
    price: Optional[Union[str, int]],
    limit_price: Optional[Union[str, int]],
    order_type: Optional[TpSlOrderType],
) -> None:
    """Enforce the server's conditional rules on one TP or SL leg."""
    if _is_set(price):
        if order_type not in (constants.TPSL_ORDER_TYPE_LIMIT, constants.TPSL_ORDER_TYPE_MARKET):
            raise ValueError(
                f"{leg}_order_type is required when {leg}_price is set: "
                f"{constants.TPSL_ORDER_TYPE_LIMIT} = limit, {constants.TPSL_ORDER_TYPE_MARKET} = market"
            )
        if not _is_set(limit_price):
            raise ValueError(
                f"{leg}_limit_price must be > 0 when {leg}_price is set — a market "
                f"{leg} needs the price to cross to, a limit one its resting price"
            )
    elif order_type:
        raise ValueError(f"{leg}_order_type without {leg}_price; set the trigger price or drop the order type")


def _put_tpsl_leg(
    request: OrderRequest,
    leg: str,
    price: Optional[Union[str, int]],
    limit_price: Optional[Union[str, int]],
    size: Optional[Union[str, int]],
    trigger_type: TriggerType,
    order_type: Optional[TpSlOrderType],
) -> None:
    """Validate and copy one inline TP/SL leg into an :class:`OrderRequest`.

    The keys are built from ``leg``, so this writes through a plain-dict view of
    the TypedDict.
    """
    _check_tpsl_leg(leg, price, limit_price, order_type)
    if not _is_set(price):
        return
    fields = cast(Dict[str, Any], request)
    fields[f"{leg}_price"] = str(price)
    fields[f"{leg}_limit_price"] = str(limit_price)
    fields[f"{leg}_size"] = str(size if size is not None else 0)
    fields[f"{leg}_trigger_type"] = trigger_type
    fields[f"{leg}_order_type"] = order_type


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
            "price is required for every order, including market and trigger "
            "orders: the wire protocol has no server-side price derivation"
        )
    if not _is_set(order["price"]):
        # A market order is not price-band checked, so a price that scaled down
        # to 0 would sweep the book rather than be rejected.
        raise ValueError(f"price must be a positive raw integer string, got {order['price']!r}")
    wire["p"] = str(order["price"])
    trigger_px = order.get("trigger_px")
    if trigger_px is not None:
        tpsl = order.get("trigger_tpsl")
        if tpsl not in (constants.TPSL_TAKE_PROFIT, constants.TPSL_STOP_LOSS):
            raise ValueError(
                f"trigger_tpsl must be {constants.TPSL_TAKE_PROFIT!r} or "
                f"{constants.TPSL_STOP_LOSS!r} — it sets the comparison direction"
            )
        wire["t"] = {
            "trigger": {
                "triggerPx": str(trigger_px),
                "isMarket": bool(order.get("trigger_is_market", True)),
                "tpsl": tpsl,
            }
        }
    elif order.get("is_market"):
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
    _tpsl_to_wire(order, wire)
    return wire


def _tpsl_to_wire(order: OrderRequest, wire: Dict[str, Any]) -> None:
    """Copy the entry-inline TP/SL legs of ``order`` onto its wire object."""
    fields = cast(Dict[str, Any], order)  # keys are built from the leg name
    legs = [leg for leg in ("tp", "sl") if _is_set(fields.get(f"{leg}_price"))]
    if not legs:
        return
    if "trigger_px" in order:
        raise ValueError("a trigger order cannot carry inline TP/SL; those belong on a limit or market entry")
    if order.get("reduce_only"):
        raise ValueError("a reduce-only order cannot carry inline TP/SL; the whole order would be rejected")
    for leg in legs:
        _check_tpsl_leg(
            leg,
            fields.get(f"{leg}_price"),
            fields.get(f"{leg}_limit_price"),
            fields.get(f"{leg}_order_type"),
        )
        wire[f"{leg}Price"] = str(fields[f"{leg}_price"])
        wire[f"{leg}LimitPrice"] = str(fields[f"{leg}_limit_price"])
        wire[f"{leg}Size"] = str(fields.get(f"{leg}_size", "0"))
        wire[f"{leg}TriggerType"] = fields.get(f"{leg}_trigger_type", constants.TRIGGER_TYPE_MARK)
        wire[f"{leg}OrderType"] = fields[f"{leg}_order_type"]
