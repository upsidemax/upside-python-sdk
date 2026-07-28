"""Register a new account on QA and wait for the 10,000 USDC test airdrop.

Set ``private_key`` and ``invite_code`` in config.json first. On success the
returned accountId is printed — save it back into config.json for later runs.
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

    print("waiting for margin to become available (airdrop ~10s)...")
    for _ in range(30):
        acct = info.user_account(account_id, md)
        if int(acct.get("marginAvailableForOrder", "0")) > 0:
            print("margin available:", acct["marginAvailableForOrder"])
            break
        # If funds landed as a chain balance, lock them into the market deployer.
        overview = info.user_account(account_id, 0)
        chain = next((c for c in overview.get("chainBalances", []) if c["coinId"] == usdc), None)
        if chain and int(chain["amount"]) > 0:
            exchange.lock_collateral(md, usdc, chain["amount"])
        time.sleep(1)
    else:
        print("airdrop not yet reflected; try again shortly")


if __name__ == "__main__":
    main()
