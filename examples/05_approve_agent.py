"""Approve a fresh agent (API) wallet, then trade with it while the master stays offline.

``approve_agent()`` without an address generates a new hot key and returns it.
The agent signs order actions; the server books them under the master account.
"""

import example_utils

from upside import Exchange


def main() -> None:
    config, info, master = example_utils.setup()

    response, agent_key = master.approve_agent(agent_name="example-bot")
    print("approveAgent:", response["response"]["data"])
    assert agent_key is not None
    print("generated agent key (store securely):", agent_key)

    # The agent trades on the master's account; account_id still points at the master.
    agent = Exchange(agent_key, base_url=master.base_url, account_id=master.account_id)
    contract = example_utils.pick_contract(info)
    print("agent order submit:", agent.order(asset=contract["contractId"], is_buy=True, size="1", price="1"))

    # Revoke when done (master-only).
    print("revokeAgent:", master.revoke_agent(agent.address)["response"]["data"])


if __name__ == "__main__":
    main()
