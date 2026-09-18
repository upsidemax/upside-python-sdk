"""Stream realtime data: public l2Book/trades plus private orderUpdates/userFills.

Private channels take the account **wallet address** in the ``user`` field.
Balances and positions stream too, on ``userAccount`` — one frame every 3s per
``(address, market deployer)``, each a full snapshot to overwrite local state
with.
"""

import time

import example_utils


def main() -> None:
    _, info, exchange = example_utils.setup(skip_ws=False)
    contract = example_utils.pick_contract(info)
    asset = str(contract["contractId"])
    address = exchange.address

    info.subscribe({"type": "l2Book", "asset": asset}, lambda m: print("l2Book book v", m["data"]["bookVersion"]))
    info.subscribe({"type": "trades", "asset": asset}, lambda m: print("trades:", len(m["data"]), "fills"))
    info.subscribe({"type": "orderUpdates", "user": address}, lambda m: print("orderUpdates:", m["data"]))
    info.subscribe({"type": "userFills", "user": address}, lambda m: print("userFills:", m["data"]))
    info.subscribe(
        {"type": "userAccount", "user": address, "marketDeployerId": contract["marketDeployerId"]},
        lambda m: print("userAccount equity:", m["data"]["crossEquity"]),
    )
    info.subscribe({"type": "ticker", "asset": asset}, lambda m: print("ticker last:", m["data"]["lastPx"]))

    print("streaming for 20s (Ctrl-C to stop)...")
    try:
        time.sleep(20)
    except KeyboardInterrupt:
        pass
    finally:
        info.close()


if __name__ == "__main__":
    main()
