"""Upside Python SDK — REST (`/info`, `/exchange`) and WebSocket client.

Quick start::

    from upside import Info, Exchange
    from upside.utils import constants

    info = Info(base_url=constants.QA_API_URL)
    print(info.configs())

    exchange = Exchange(private_key, base_url=constants.QA_API_URL)
    exchange.order(asset=1, is_buy=True, size="10", price="50")

See https://docs.upsidemax.xyz for the full API reference.
"""

from .api import API
from .exchange import Exchange
from .info import Info
from .utils import constants
from .utils.error import APIError, ClientError, ServerError, UpsideError, WebsocketError
from .utils.signing import NonceManager
from .utils.types import Cloid
from .websocket_manager import WebsocketManager

__version__ = "0.1.0"

__all__ = [
    "API",
    "Info",
    "Exchange",
    "WebsocketManager",
    "NonceManager",
    "Cloid",
    "constants",
    "UpsideError",
    "APIError",
    "ClientError",
    "ServerError",
    "WebsocketError",
    "__version__",
]
