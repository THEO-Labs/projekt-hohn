import pytest
from app.companies.isin import accounting_standard, is_us_isin, validate_isin


@pytest.mark.parametrize("isin", [
    "US0378331005",  # Apple
    "DE000BASF111",  # BASF
    "GB0002634946",  # BAE Systems
    "US4592001014",  # IBM
])
def test_valid_isins(isin):
    assert validate_isin(isin) is True


@pytest.mark.parametrize("isin,reason", [
    ("US0378331006", "wrong check digit"),
    ("AAPL", "too short"),
    ("XX1234567890", "malformed"),
    ("", "empty string"),
    ("us0378331005", "lowercase country code"),
    ("US037833100", "only 11 chars"),
    ("US03783310055", "13 chars"),
    ("U10378331005", "first char not letter"),
    ("1S0378331005", "second char not letter"),
    ("US037833100A", "last char not digit"),
])
def test_invalid_isins(isin, reason):
    assert validate_isin(isin) is False, f"Expected invalid for: {reason}"


@pytest.mark.parametrize("isin,expected_us,expected_standard", [
    ("US0378331005", True, "US-GAAP"),   # Apple
    ("us0378331005", True, "US-GAAP"),   # Praefix case-insensitiv
    ("DE000BASF111", False, "IFRS"),     # BASF
    ("GB0002634946", False, "IFRS"),     # BAE Systems
    (None, False, "IFRS"),               # ohne ISIN -> IFRS
    ("", False, "IFRS"),
])
def test_accounting_standard_from_isin(isin, expected_us, expected_standard):
    assert is_us_isin(isin) is expected_us
    assert accounting_standard(isin) == expected_standard
