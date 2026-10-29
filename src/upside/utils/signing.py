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
from typing import Any, Dict, Optional, Union, cast

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


def _domain_separator(chain_id: int) -> bytes:
    """EIP-712 domain: Exchange / v1 / ``chain_id`` / verifyingContract 0x0."""
    return keccak(
        keccak(b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
        + keccak(constants.DOMAIN_NAME.encode())
        + keccak(constants.DOMAIN_VERSION.encode())
        + _u(chain_id)
        + b"\x00" * 32
    )


# The Mainnet domain (chainId 9788), the default when no chain id is given.
_DOMAIN_SEPARATOR = _domain_separator(constants.CHAIN_ID)

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

# A typed action whose struct depends on which optional fields are present:
# ``registerAccount`` carrying ``marketDeployerId`` (any value, 0 included) is
# signed over the second struct.
_TYPED_WITH_FIELD: Dict[str, Dict[str, str]] = {
    "registerAccount": {"marketDeployerId": "RegisterAccount(address address,uint32 marketDeployerId,uint64 nonce)"},
}

# Optional typed fields MUST still appear in the wire JSON — the server reads
# them by name to recompute the digest. Inject defaults before signing so the
# signed struct and the sent JSON match, otherwise recovery yields a different
# address (401 SIGNATURE_INVALID).
_TYPED_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "approveAgent": {"agentName": "", "validUntil": 0},
}


def is_typed_action(action_type: str) -> bool:
    """Whether ``action_type`` signs a field-level typed struct (vs the Agent path)."""
    return action_type in _TYPED


def _encode_field(sol_type: str, name: str, value: Any) -> bytes:
    if sol_type == "address":
        return _addr(value)
    if sol_type == "string":
        return _string(value)
    # uintN: the server hashes these only when the wire value is a JSON integer,
    # so a string or bool here would sign fine and still fail SIGNATURE_INVALID.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(f"{name} must be a non-negative int, got {value!r}")
    return _u(value)


def _typed_struct(type_str: str, action: Dict[str, Any], nonce: int) -> bytes:
    fields = [f.split() for f in type_str[type_str.index("(") + 1 : -1].split(",")]
    encoded = [_encode_field(sol_type, name, nonce if name == "nonce" else action[name]) for sol_type, name in fields]
    return keccak(keccak(type_str.encode()) + b"".join(encoded))


def action_hash(action: Dict[str, Any], nonce: int) -> bytes:
    """Agent-path ``actionHash`` = keccak(canonicalJson ‖ nonce_be8).

    Canonical JSON: keys sorted, no whitespace, non-ASCII text as raw UTF-8 (the
    server does not ``\\u``-escape it), control characters escaped.
    """
    canonical = json.dumps(action, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return keccak(canonical + int(nonce).to_bytes(8, "big"))


def _domain(chain_id: int) -> bytes:
    return _DOMAIN_SEPARATOR if chain_id == constants.CHAIN_ID else _domain_separator(chain_id)


def eip712_digest(
    action: Dict[str, Any],
    nonce: int,
    signature_chain_id: Optional[int] = None,
    chain_id: int = constants.CHAIN_ID,
) -> bytes:
    """Compute the 32-byte EIP-712 digest for an action.

    For typed actions this mutates ``action`` to fill required-but-optional
    fields (e.g. ``agentName``) so the sent JSON matches the signed struct.

    ``chain_id`` is the environment's chain id (Devnet 9767, Mainnet 9788).
    ``signature_chain_id`` overrides it for **typed** actions only
    (browser-extension wallets sign with their active chain); it must then also
    travel in the envelope's top-level ``signatureChainId``. Agent-path actions
    always use ``chain_id`` and ignore it.
    """
    type_str = _TYPED.get(action["type"])
    for field, variant in _TYPED_WITH_FIELD.get(action["type"], {}).items():
        if field in action:
            type_str = variant
    if type_str is not None:
        for key, default in _TYPED_DEFAULTS.get(action["type"], {}).items():
            action.setdefault(key, default)
        struct = _typed_struct(type_str, action, nonce)
        domain = _domain(chain_id if signature_chain_id is None else signature_chain_id)
    else:
        struct = keccak(
            keccak(b"Agent(string source,bytes32 actionHash)")
            + keccak(constants.AGENT_SOURCE.encode())
            + action_hash(action, nonce)
        )
        domain = _domain(chain_id)
    return keccak(b"\x19\x01" + domain + struct)


def _sign_digest(wallet: Wallet, digest: bytes) -> Any:
    """Sign a 32-byte digest directly (``prehash = false``), across eth-account versions."""
    signer = getattr(Account, "unsafe_sign_hash", None) or Account._sign_hash
    return signer(digest, wallet.key)


def sign_action(
    wallet: Wallet,
    action: Dict[str, Any],
    nonce: int,
    signature_chain_id: Optional[int] = None,
    chain_id: int = constants.CHAIN_ID,
) -> Signature:
    """Return the ``{"r", "s", "v"}`` signature envelope for ``action``.

    ``r``/``s`` are ``0x``-prefixed lowercase 32-byte hex; ``v`` is 27 or 28.
    ``chain_id`` / ``signature_chain_id``: see :func:`eip712_digest`.
    """
    signed = _sign_digest(wallet, eip712_digest(action, nonce, signature_chain_id, chain_id))
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

    The server needs each signer's nonces to be unique and within
    ``(block time - 2 days, block time + 1 day)``; it does not need them to be
    increasing. A nonce still in flight is refused with ``400 DUPLICATE_NONCE``;
    a used, too old, or too new one is rejected by the engine. Sub-millisecond
    bursts are handled by incrementing past the last issued value.

    Uniqueness is per instance: share one manager between every ``Exchange``
    signing with the same key, and do not run two processes on one key without
    partitioning the nonce space.
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
    "is_typed_action",
    "sign_action",
    "to_wallet",
]
