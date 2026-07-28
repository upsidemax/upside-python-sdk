"""EIP-712 request signing for ``POST /exchange``.

Every write is authorized by an ECDSA signature (secp256k1) over an EIP-712
digest; the server recovers the signer's address — there are no API keys. Two
signing paths, selected by ``action["type"]``:

* **Typed path** — the six funds/permission actions, each with a field-level
  struct so a wallet can render human-readable values.
* **Agent path** — everything else. The whole canonical-JSON action is folded
  into a single ``actionHash`` carried by ``Agent(string source, bytes32 actionHash)``.

This is a direct port of the reference implementation published at
https://docs.upsidemax.xyz/guide/authentication — the digest formula must match
the server byte-for-byte or recovery fails with ``SIGNATURE_INVALID``.
"""

import json
import threading
import time
from typing import Any, Dict, Union, cast

from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_utils import keccak

from . import constants

Wallet = LocalAccount
Signature = Dict[str, Any]


def _u(value: Union[int, str]) -> bytes:
    """Encode an unsigned integer as a 32-byte big-endian word."""
    return int(value).to_bytes(32, "big")


def _addr(value: str) -> bytes:
    """Encode an address as 20 bytes left-padded to 32."""
    hex_body = value[2:] if value[:2].lower() == "0x" else value
    return b"\x00" * 12 + bytes.fromhex(hex_body)


def _string(value: Any) -> bytes:
    """Encode a string field as ``keccak256(utf8Bytes)``."""
    return keccak(str(value).encode())


# EIP-712 domain separator: Exchange / v1 / chainId 9767 / verifyingContract 0x0.
_DOMAIN_SEPARATOR = keccak(
    keccak(b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
    + keccak(constants.DOMAIN_NAME.encode())
    + keccak(constants.DOMAIN_VERSION.encode())
    + _u(constants.CHAIN_ID)
    + b"\x00" * 32
)

# Funds / permission actions sign a field-level typed struct. Field names match
# the action's JSON keys, so encodings are derived straight from the type string.
_TYPED: Dict[str, str] = {
    "registerAccount": "RegisterAccount(address address,uint64 nonce)",
    "approveAgent": "ApproveAgent(address agentAddress,string agentName,uint64 validUntil,uint64 nonce)",
    "revokeAgent": "RevokeAgent(address agentAddress,uint64 nonce)",
    "lockCollateral": "LockCollateral(uint32 marketDeployerId,uint32 coinId,string amount,uint64 nonce)",
    "unlockCollateral": "UnlockCollateral(uint32 marketDeployerId,uint32 coinId,string amount,uint64 nonce)",
    "transferBetweenDeployers": (
        "TransferBetweenDeployers(uint32 fromMarketDeployerId,uint32 toMarketDeployerId,"
        "uint32 coinId,string amount,uint64 nonce)"
    ),
}

# Optional typed fields MUST still appear in the wire JSON — the server reads
# them by name to recompute the digest. Inject defaults before signing so the
# signed struct and the sent JSON match, otherwise recovery yields a different
# address (401 SIGNATURE_INVALID).
_TYPED_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "approveAgent": {"agentName": "", "validUntil": 0},
}


def _encode_field(sol_type: str, value: Any) -> bytes:
    if sol_type == "address":
        return _addr(value)
    if sol_type == "string":
        return _string(value)
    return _u(value)  # uintN


def _typed_struct(type_str: str, action: Dict[str, Any], nonce: int) -> bytes:
    fields = [f.split() for f in type_str[type_str.index("(") + 1 : -1].split(",")]
    encoded = [_encode_field(sol_type, nonce if name == "nonce" else action[name]) for sol_type, name in fields]
    return keccak(keccak(type_str.encode()) + b"".join(encoded))


def action_hash(action: Dict[str, Any], nonce: int) -> bytes:
    """Agent-path ``actionHash`` = keccak(canonicalJson ‖ nonce_be8)."""
    canonical = json.dumps(action, sort_keys=True, separators=(",", ":")).encode()
    return keccak(canonical + int(nonce).to_bytes(8, "big"))


def eip712_digest(action: Dict[str, Any], nonce: int) -> bytes:
    """Compute the 32-byte EIP-712 digest for an action.

    For typed actions this mutates ``action`` to fill required-but-optional
    fields (e.g. ``agentName``) so the sent JSON matches the signed struct.
    """
    type_str = _TYPED.get(action["type"])
    if type_str is not None:
        for key, default in _TYPED_DEFAULTS.get(action["type"], {}).items():
            action.setdefault(key, default)
        struct = _typed_struct(type_str, action, nonce)
    else:
        struct = keccak(
            keccak(b"Agent(string source,bytes32 actionHash)")
            + keccak(constants.AGENT_SOURCE.encode())
            + action_hash(action, nonce)
        )
    return keccak(b"\x19\x01" + _DOMAIN_SEPARATOR + struct)


def _sign_digest(wallet: Wallet, digest: bytes) -> Any:
    """Sign a 32-byte digest directly (``prehash = false``), across eth-account versions."""
    signer = getattr(Account, "unsafe_sign_hash", None) or Account._sign_hash
    return signer(digest, wallet.key)


def sign_action(wallet: Wallet, action: Dict[str, Any], nonce: int) -> Signature:
    """Return the ``{"r", "s", "v"}`` signature envelope for ``action``.

    ``r``/``s`` are ``0x``-prefixed lowercase 32-byte hex; ``v`` is 27 or 28.
    """
    signed = _sign_digest(wallet, eip712_digest(action, nonce))
    return {
        "r": "0x" + int(signed.r).to_bytes(32, "big").hex(),
        "s": "0x" + int(signed.s).to_bytes(32, "big").hex(),
        "v": signed.v if signed.v >= 27 else signed.v + 27,
    }


def to_wallet(wallet_or_key: Union[Wallet, str]) -> Wallet:
    """Coerce a private-key hex string into a :class:`LocalAccount`."""
    if isinstance(wallet_or_key, str):
        return cast(Wallet, Account.from_key(wallet_or_key))
    return wallet_or_key


class NonceManager:
    """Thread-safe, strictly increasing millisecond nonce source.

    Each signing address needs unique, monotonic nonces; the server rejects a
    reused or regressing nonce with ``NONCE_REUSED``. Sub-millisecond bursts are
    handled by incrementing past the last issued value.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last = 0

    def next(self) -> int:
        with self._lock:
            self._last = max(self._last + 1, int(time.time() * 1000))
            return self._last


__all__ = [
    "Wallet",
    "Signature",
    "NonceManager",
    "action_hash",
    "eip712_digest",
    "sign_action",
    "to_wallet",
]
