"""Read market data with the Info client: configs, market state, book, candles."""

import example_utils


def main() -> None:
    _, info, _ = example_utils.setup()

    contract = example_utils.pick_contract(info)
    asset = contract["contractId"]
    print(f"contract {asset} {contract['name']} priceScale={contract['priceScale']} qtyScale={contract['qtyScale']}")

    state = info.market_state(asset)
    print("market state:", {k: state[k] for k in ("markPx", "oraclePx", "lastPx", "priceReady")})

    try:
        book = info.l2_book(asset)
        print(f"book v{book['bookVersion']}: {len(book['levels'][0])} bids / {len(book['levels'][1])} asks")
    except Exception as err:  # 503 NOT_READY before any book exists
        print("l2_book not ready:", err)

    candles = info.candle_snapshot(asset, "1m").get("candles", [])
    print(f"candles returned: {len(candles)}")


if __name__ == "__main__":
    main()
