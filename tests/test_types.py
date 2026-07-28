"""Cloid validation and normalization tests."""

import pytest

from upside.utils.types import Cloid, as_cloid_str


def test_cloid_roundtrip():
    c = Cloid.from_int(1778763737044)
    assert c.to_raw() == "1778763737044"
    assert c.to_int() == 1778763737044
    assert str(c) == "1778763737044"


def test_cloid_rejects_non_positive():
    with pytest.raises(ValueError):
        Cloid("0")
    with pytest.raises(ValueError):
        Cloid.from_int(-1)


def test_cloid_rejects_out_of_int64_range():
    with pytest.raises(ValueError):
        Cloid.from_int(2**63)


def test_cloid_equality_and_hash():
    assert Cloid("5") == Cloid.from_int(5)
    assert len({Cloid("5"), Cloid.from_int(5)}) == 1


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (42, "42"),
        ("42", "42"),
        (Cloid.from_int(42), "42"),
    ],
)
def test_as_cloid_str(value, expected):
    assert as_cloid_str(value) == expected
