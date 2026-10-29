"""Register a new account and check whether it has usable margin.

Set ``private_key`` and ``invite_code`` in config.json first. On success the
returned accountId is printed — save it back into config.json for later runs.
A new account starts with no balance: fund it before running the trading
examples. Funds already held as a chain-level balance are moved into the market
deployer with ``lock_collateral``.
"""

import time

import example_utils

from upside import ClientError


def main() -> None:
    config, info, exchange = example_utils.setup()
    md = config["market_deployer_id"]
    usdc = config["usdc_coin_id"]

    try:
        result = exchange.register_account(invite_code=config.get("invite_code") or None)
        account_id = result["response"]["accountId"]
        print(f"registered accountId={account_id}")
    except ClientError as err:
        if err.code == "ACCOUNT_ALREADY_EXISTS":
            print("account already registered; continuing")
        else:
            raise

    account_id = exchange.account_id
    assert account_id is not None, "no accountId — registration failed"

    def margin() -> int:
        return int(info.user_account(account_id, md).get("marginAvailableForOrder", "0"))

    if margin() == 0:
        overview = info.user_account(account_id, 0)
        chain = next((c for c in overview.get("chainBalances", []) if c["coinId"] == usdc), None)
        if chain and int(chain["amount"]) > 0:
            exchange.lock_collateral(md, usdc, chain["amount"])
            for _ in range(10):
                if margin() > 0:
                    break
                time.sleep(1)

    available = margin()
    if available > 0:
        print("margin available:", available)
    else:
        print(f"account {account_id} has no usable margin — fund it before trading")


if __name__ == "__main__":
    main()
