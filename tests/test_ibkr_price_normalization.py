from ibkr.utils.ibkr_price import first_positive_ibkr_price, is_ibkr_unset_price, safe_ibkr_price


IBKR_UNSET_DOUBLE = 1.7976931348623157e308


def test_safe_ibkr_price_maps_ibkr_unset_double_to_default():
    assert is_ibkr_unset_price(IBKR_UNSET_DOUBLE)
    assert safe_ibkr_price(IBKR_UNSET_DOUBLE) == 0.0


def test_first_positive_ibkr_price_skips_unset_sentinel():
    assert first_positive_ibkr_price(IBKR_UNSET_DOUBLE, None, 42.25) == 42.25


def test_first_positive_ibkr_price_returns_zero_when_only_unset_or_nonpositive():
    assert first_positive_ibkr_price(IBKR_UNSET_DOUBLE, 0, -1) == 0.0
