"""Tests for the truncate_id helper in collector.__main__."""

from collector.__main__ import truncate_id


def test_long_id_is_truncated():
    """78-digit Polymarket token ID gets shortened."""
    long_id = "1" * 78
    result = truncate_id(long_id)
    assert result == "111111111111...1111"
    assert len(result) == 19  # 12 + 3 + 4


def test_short_id_unchanged():
    """IDs at or below threshold are returned as-is."""
    short = "abc123"
    assert truncate_id(short) == "abc123"


def test_boundary_length_unchanged():
    """ID of exactly length + 4 chars is not truncated."""
    val = "a" * 16  # default length=12, 12+4=16
    assert truncate_id(val) == val


def test_boundary_plus_one_truncated():
    """ID of length + 5 chars triggers truncation."""
    val = "a" * 17
    assert truncate_id(val) == "aaaaaaaaaaaa...aaaa"


def test_custom_length():
    """Custom length parameter is respected."""
    val = "abcdefghijklmnopqrstuvwxyz"  # 26 chars
    result = truncate_id(val, length=6)
    assert result == "abcdef...wxyz"


def test_tx_hash_truncated():
    """66-char hex transaction hash gets shortened."""
    tx = "0x" + "ab" * 32  # 66 chars
    result = truncate_id(tx)
    assert result.startswith("0xababababab")
    assert result.endswith("abab")
    assert "..." in result
