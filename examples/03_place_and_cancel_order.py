"""Place a resting limit order (with a client order id), read it back, then cancel it.

Order placement is asynchronous — the exchange responds ``accepted`` and the
order id is read from ``userOrders``. Prices/sizes are raw integer strings.
"""

import time

import example_utils

from upside import Cloid


def main() -> None:
    config, info, exchange = example_utils.setup()
    md = config["market_deployer_id"]
    account_id = exchange.account_id
    assert account_id is not None, "run 01_register_and_airdrop.py first and set account_id"

    contract = example_utils.pick_contract(info)
    asset = contract["contractId"]

    cloid = Cloid.from_int(int(time.time() * 1000))
    print("placing limit buy, cloid:", cloid)
    accepted = exchange.order(asset=asset, is_buy=True, size="1", price="1", cloid=cloid)
    print("submit response:", accepted)

    time.sleep(1)
    found = info.orders_by_cloids(account_id, md, [cloid.to_raw()]).get("orders", [])
    if not found:
        print("order not found by cloid yet; check userOrders")
        return
    order = found[0]
    print(f"resting order id={order['id']} price={order['price']} size={order['size']} status={order['status']}")

    print("cancel:", exchange.cancel_by_cloid(asset, cloid))


if __name__ == "__main__":
    main()
