"""Deterministic signature-regression tests.

These pin the EIP-712 digest and (r, s, v) output for a fixed private key so a
refactor can't silently change the wire/signing format and break server-side
recovery. They also assert round-trip recovery for both signing paths.
"""

import json

import pytest
from eth_account import Account

from upside.utils import constants, signing
from upside.utils.signing import NonceManager

# Fixed test key (never used with real funds) and a fixed nonce.
PRIVATE_KEY = "0x" + "01" * 32
ADDRESS = "0x1a642f0e3c3af545e7acbd38b07251b3990914f1"
NONCE = 1743600000000

REGISTER_DIGEST = "0xb2be181b6df4c6d36fa56ca773db42945fcc71e7f757815261cecb9a8fb8953b"
REGISTER_SIG = {
    "r": "0xee30c2ea7ab0c3430394df311db1ae82def6698347bc00c793d5b852c54ccf69",
    "s": "0x3c64b1b04b743a0a75276384d6f88f89cff75b02f47146f63e009f9fd5d9ec69",
    "v": 27,
}
ORDER_DIGEST = "0x433f26a465507255b1c79979e99c40aaa52506391d6d1ee73302d459bb9fa18d"
ORDER_SIG = {
    "r": "0x4df3bd1df144369b729118d0861d7e9c801d2dc0514be202ba0668820f14787c",
    "s": "0x7a0c0b33c3424d951d786579a9a679cfb7f09b9c5dec518c03c67ccad1b8ef92",
    "v": 27,
}

ORDER_ACTION = {
    "type": "order",
    "grouping": "na",
    "orders": [{"a": 1, "b": True, "p": "50", "s": "10", "r": False, "t": {"limit": {"tif": "Gtc"}}}],
}


@pytest.fixture
def wallet():
    return Account.from_key(PRIVATE_KEY)


def _recover(digest: bytes, sig: dict) -> str:
    return Account._recover_hash(digest, vrs=(sig["v"], int(sig["r"], 16), int(sig["s"], 16))).lower()


def test_address_matches(wallet):
    assert wallet.address.lower() == ADDRESS


def test_domain_separator_uses_chain_id_9767():
    # Sanity: the domain constant is built from chainId 9767 (regression guard).
    assert constants.CHAIN_ID == 9767
    assert len(signing._DOMAIN_SEPARATOR) == 32


def test_typed_path_digest_and_signature_are_stable(wallet):
    action = {"type": "registerAccount", "address": ADDRESS}
    assert "0x" + signing.eip712_digest(dict(action), NONCE).hex() == REGISTER_DIGEST
    assert signing.sign_action(wallet, dict(action), NONCE) == REGISTER_SIG


def test_agent_path_digest_and_signature_are_stable(wallet):
    assert "0x" + signing.eip712_digest(dict(ORDER_ACTION), NONCE).hex() == ORDER_DIGEST
    assert signing.sign_action(wallet, dict(ORDER_ACTION), NONCE) == ORDER_SIG


def test_typed_path_recovers_signer(wallet):
    action = {"type": "registerAccount", "address": ADDRESS}
    digest = signing.eip712_digest(dict(action), NONCE)
    sig = signing.sign_action(wallet, dict(action), NONCE)
    assert _recover(digest, sig) == ADDRESS


def test_agent_path_recovers_signer(wallet):
    digest = signing.eip712_digest(dict(ORDER_ACTION), NONCE)
    sig = signing.sign_action(wallet, dict(ORDER_ACTION), NONCE)
    assert _recover(digest, sig) == ADDRESS


def test_approve_agent_injects_typed_defaults():
    action = {"type": "approveAgent", "agentAddress": "0x" + "ab" * 20}
    signing.eip712_digest(action, NONCE)
    assert action["agentName"] == ""
    assert action["validUntil"] == 0


def test_action_hash_uses_canonical_json():
    # Key order and whitespace must not affect the Agent-path hash.
    a = {"type": "order", "grouping": "na", "orders": []}
    b = {"orders": [], "type": "order", "grouping": "na"}
    assert signing.action_hash(a, NONCE) == signing.action_hash(b, NONCE)
    canonical = json.dumps(a, sort_keys=True, separators=(",", ":"))
    assert canonical == '{"grouping":"na","orders":[],"type":"order"}'


def test_v_is_normalized_to_27_or_28(wallet):
    sig = signing.sign_action(wallet, dict(ORDER_ACTION), NONCE)
    assert sig["v"] in (27, 28)
    assert sig["r"].startswith("0x") and len(sig["r"]) == 66


def test_nonce_manager_is_strictly_increasing():
    nm = NonceManager()
    values = [nm.next() for _ in range(1000)]
    assert values == sorted(values)
    assert len(set(values)) == len(values)
