"""Adjust leverage and attach position-level take-profit / stop-loss triggers.

Both actions respond synchronously; rejections come back as HTTP 200 with a
non-zero ``errorCode`` inside ``response.data`` — always inspect it.
"""

import example_utils


def main() -> None:
    config, info, exchange = example_utils.setup()
    contract = example_utils.pick_contract(info)
    asset = contract["contractId"]

    lev = exchange.update_leverage(asset, 20)
    print("update_leverage:", lev["response"]["data"])

    # Position-level TP/SL: close direction is inferred from the position side.
    # Each leg needs the price of the order placed on trigger (*_limit_price)
    # and what kind of order that is (*_order_type: 1 = limit, 2 = market).
    tpsl = exchange.tp_sl(
        asset=asset,
        tp_price="90000",
        tp_limit_price="90000",
        tp_order_type=1,
        sl_price="80000",
        sl_limit_price="79000",
        sl_order_type=2,
        is_position_tpsl=True,
    )
    print("tp_sl:", tpsl["response"]["data"])

    print("cancel_tp_sl:", exchange.cancel_tp_sl(asset)["response"]["data"])


if __name__ == "__main__":
    main()
