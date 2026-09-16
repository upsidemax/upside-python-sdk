"""Shared bootstrap for the example scripts.

Copy ``config.json.example`` to ``config.json`` and fill in your test wallet's
``private_key`` and (for UAT) an ``invite_code`` from the Upside team. All
examples run against the UAT testnet by default.
"""

import json
import os
from typing import Any, Dict, Optional, Tuple

from upside import Exchange, Info
from upside.utils import constants

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def load_config() -> Dict[str, Any]:
    if not os.path.exists(_CONFIG_PATH):
        raise SystemExit("examples/config.json not found — copy config.json.example to config.json and fill it in.")
    with open(_CONFIG_PATH) as fh:
        return json.load(fh)


def setup(skip_ws: bool = True) -> Tuple[Dict[str, Any], Info, Exchange]:
    """Return ``(config, info, exchange)`` wired to the configured environment."""
    config = load_config()
    base_url = config.get("base_url") or constants.UAT_API_URL
    info = Info(base_url=base_url, skip_ws=skip_ws)
    exchange = Exchange(config["private_key"], base_url=base_url, account_id=config.get("account_id"))
    return config, info, exchange


def pick_contract(info: Info, quote_coin_id: Optional[int] = None) -> Dict[str, Any]:
    """Return the first Active contract, optionally filtered by quote coin.

    Contract IDs are server-assigned — always resolve them from ``configs``.
    """
    contracts = [c for c in info.configs()["contracts"] if c["status"] == "Active"]
    if quote_coin_id is not None:
        contracts = [c for c in contracts if c["quoteCoinId"] == quote_coin_id]
    if not contracts:
        raise SystemExit("no active contracts found via configs")
    return contracts[0]
