"""EIP-712 digests pinned by the server's own golden-vector tests.

Values are copied from l1chain ``Eip712TypedGoldenVectorTest`` and
``CanonicalGoldenVectorTest`` (origin/qa, 2026-10-23). A mismatch here means the
server would recover a different signer: ``401 SIGNATURE_INVALID``.
"""

import pytest

from upside.utils.signing import action_hash, eip712_digest

NONCE = 1788825823650
A = "0xabababababababababababababababababababab"
B = "0xCDCDcdcdCDCDcdcdCDCDcdcdCDCDcdcdCDCDcdcd"

VECTORS = [
    ({"type": "registerAccount", "address": A}, "ed171024a1b3c1bf7803a828b750eca29ea94b4836ed395c633d26ab22206bff"),
    (
        {"type": "registerAccount", "address": A, "marketDeployerId": 1},
        "894087d55abea35ec4827d3606453811c4fdfa5843d05dcc156265f550888e07",
    ),
    (
        {"type": "registerAccount", "address": A, "marketDeployerId": 0},
        "ac893d655592d91e9db876478510be3f180dac2628228ad8bfeaabc7e1e11701",
    ),
    (
        {"type": "approveAgent", "agentAddress": B, "agentName": "bot-1", "validUntil": 1788912223650},
        "8b15dbf101d51f96cde3ec4f6a3e6af23cbbd3728f202ce7cd8cc02e10f4507c",
    ),
    ({"type": "revokeAgent", "agentAddress": B}, "e6386cb36fc31498f8ad0f8f49997e278aca39e155d7bc044ebb77aa2a0b3b4e"),
    (
        {"type": "lockCollateral", "marketDeployerId": 7, "coinId": 3, "amount": "1000000"},
        "b9d0f6c6663feffc373cb0b2c362a0ef139462859a6e1ec5458d4e111e7a15c6",
    ),
    (
        {"type": "unlockCollateral", "marketDeployerId": 7, "coinId": 3, "amount": "1000000"},
        "dfb957fe0c9d71376292bfb3f51b02aef5af7a7d9ea7c9df5523541e4af0303a",
    ),
    (
        {
            "type": "transferBetweenDeployers",
            "fromMarketDeployerId": 7,
            "toMarketDeployerId": 9,
            "coinId": 3,
            "amount": "1000000",
        },
        "3616b3864703e4622132519e3f738841ad45dfbc17621db2f7ac815267054bca",
    ),
    (
        {"type": "approveAgent", "agentAddress": B, "agentName": 'a"bé', "validUntil": 1},
        "b1cc60163d5b2195d9d6bc4940b4de8b0c7c8201ac38fc9f06a5b7783c40ce98",
    ),
    (
        {"type": "order", "coin": "BTC", "isBuy": True, "sz": "0.5", "px": "65000"},
        "e6159334f973cacc60e342be396a0377a16d2bb6890464384b4e588ef20941bc",
    ),
    (
        {"type": "submitPrice", "item": [{"coin": "BTC", "px": "65000"}]},
        "22b45bb64b5cfc488c7201549126915ec24eafe62739e86a4667d51d3e28b445",
    ),
]


@pytest.mark.parametrize("action, expected", VECTORS, ids=[f"{a['type']}-{i}" for i, (a, _) in enumerate(VECTORS)])
def test_digest_matches_server_vector(action, expected):
    assert eip712_digest(dict(action), NONCE).hex() == expected


def test_typed_uint_given_as_string_is_refused_locally():
    """The server hashes a uint only from a JSON number; a string would never verify."""
    with pytest.raises(TypeError):
        eip712_digest({"type": "registerAccount", "address": A, "marketDeployerId": "1"}, NONCE)
    with pytest.raises(TypeError):
        eip712_digest({"type": "lockCollateral", "marketDeployerId": True, "coinId": 3, "amount": "1"}, NONCE)


def test_agent_hash_keeps_non_ascii_as_raw_utf8():
    """The server's canonical JSON writes non-ASCII bytes verbatim (no \\u escapes)."""
    from eth_utils import keccak

    action = {"type": "order", "coin": "BTCé", "n": "\t"}
    canonical = '{"coin":"BTCé","n":"\\t","type":"order"}'.encode()
    assert action_hash(action, 1) == keccak(canonical + (1).to_bytes(8, "big"))
