"""Comprehensive tests for value parsing, sanity checks, and error handling."""
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.llm.claude import (
    extract_value,
    extract_research_value,
    validate_claude_value,
    _parse_numeric_string,
)
from app.providers.yahoo import YahooFinanceProvider, VALUE_SANITY_CHECKS


# ---------------------------------------------------------------------------
# _parse_numeric_string
# ---------------------------------------------------------------------------

class TestParseNumericString:
    def test_plain_integer(self):
        assert _parse_numeric_string("139947000000") == Decimal("139947000000")

    def test_us_thousands_comma(self):
        assert _parse_numeric_string("139,947,000,000") == Decimal("139947000000")

    def test_german_thousands_dot(self):
        assert _parse_numeric_string("139.947.000.000") == Decimal("139947000000")

    def test_german_decimal_comma(self):
        assert _parse_numeric_string("139.947,00") == Decimal("139947.00")

    def test_us_decimal_dot(self):
        assert _parse_numeric_string("14,770.65") == Decimal("14770.65")

    def test_mrd_dot(self):
        assert _parse_numeric_string("14.77 Mrd") == Decimal("14770000000")

    def test_mrd_comma(self):
        assert _parse_numeric_string("14,77 Mrd") == Decimal("14770000000")

    def test_b_suffix(self):
        assert _parse_numeric_string("14.77B") == Decimal("14770000000")

    def test_billion_word(self):
        assert _parse_numeric_string("14.77 billion") == Decimal("14770000000")

    def test_m_suffix(self):
        assert _parse_numeric_string("14,770.65M") == Decimal("14770650000")

    def test_mio_suffix(self):
        assert _parse_numeric_string("14.770,65 Mio") == Decimal("14770650000")

    def test_negative_mrd(self):
        assert _parse_numeric_string("-1.5 Mrd") == Decimal("-1500000000")

    def test_percent_stripped(self):
        assert _parse_numeric_string("4.38%") == Decimal("4.38")

    def test_simple_decimal(self):
        assert _parse_numeric_string("27.65") == Decimal("27.65")

    def test_empty_string(self):
        assert _parse_numeric_string("") is None

    def test_simple_negative(self):
        assert _parse_numeric_string("-1.5") == Decimal("-1.5")

    def test_t_suffix_trillion(self):
        result = _parse_numeric_string("2.5T")
        assert result == Decimal("2500000000000")

    def test_k_suffix(self):
        result = _parse_numeric_string("500K")
        assert result == Decimal("500000")


# ---------------------------------------------------------------------------
# extract_value (chat-style WERT: parsing)
# ---------------------------------------------------------------------------

class TestExtractValue:
    def test_plain_integer(self):
        assert extract_value("WERT: 139947000000") == Decimal("139947000000")

    def test_us_thousands(self):
        assert extract_value("WERT: 139,947,000,000") == Decimal("139947000000")

    def test_german_thousands(self):
        assert extract_value("WERT: 139.947.000.000") == Decimal("139947000000")

    def test_german_mixed(self):
        assert extract_value("WERT: 139.947,00") == Decimal("139947.00")

    def test_mrd_dot(self):
        assert extract_value("WERT: 14.77 Mrd") == Decimal("14770000000")

    def test_mrd_comma(self):
        assert extract_value("WERT: 14,77 Mrd") == Decimal("14770000000")

    def test_b_suffix(self):
        assert extract_value("WERT: 14.77B") == Decimal("14770000000")

    def test_b_suffix_spaced(self):
        assert extract_value("WERT: 14.77 B") == Decimal("14770000000")

    def test_m_suffix_with_thousands(self):
        assert extract_value("WERT: 14,770.65M") == Decimal("14770650000")

    def test_mio_german_format(self):
        assert extract_value("WERT: 14.770,65 Mio") == Decimal("14770650000")

    def test_negative_mrd(self):
        assert extract_value("WERT: -1.5 Mrd") == Decimal("-1500000000")

    def test_percent_value(self):
        assert extract_value("WERT: 4.38%") == Decimal("4.38")

    def test_nicht_gefunden_falls_back_to_none(self):
        assert extract_value("WERT: NICHT_GEFUNDEN") is None

    def test_no_wert_falls_back_to_score(self):
        result = extract_value("SCORE: 1.20\nBEGRUENDUNG: test")
        assert result == Decimal("1.20")

    def test_no_wert_no_score_returns_none(self):
        assert extract_value("Keine relevante Antwort") is None

    def test_multiline_context(self):
        text = "Das Ergebnis ist:\nWERT: 139,947,000,000\nQUELLE: Geschäftsbericht"
        assert extract_value(text) == Decimal("139947000000")

    def test_simple_decimal(self):
        assert extract_value("WERT: 27.65") == Decimal("27.65")


# ---------------------------------------------------------------------------
# extract_research_value
# ---------------------------------------------------------------------------

class TestExtractResearchValue:
    def test_plain_integer(self):
        assert extract_research_value("WERT: 139947000000") == Decimal("139947000000")

    def test_nicht_gefunden_returns_none(self):
        assert extract_research_value("WERT: NICHT_GEFUNDEN") is None

    def test_nicht_gefunden_space(self):
        assert extract_research_value("WERT: NICHT GEFUNDEN") is None

    def test_nicht_gefunden_lowercase(self):
        assert extract_research_value("WERT: nicht_gefunden") is None

    def test_mrd_suffix(self):
        assert extract_research_value("WERT: 14.77 Mrd\nQUELLE: Geschäftsbericht") == Decimal("14770000000")

    def test_negative_value(self):
        assert extract_research_value("WERT: -5000000000") == Decimal("-5000000000")

    def test_decimal_value(self):
        assert extract_research_value("WERT: 4.38\nEINHEIT: %") == Decimal("4.38")

    def test_no_wert_returns_none(self):
        assert extract_research_value("Keine Antwort gefunden") is None

    def test_b_suffix(self):
        assert extract_research_value("WERT: 2.5B") == Decimal("2500000000")

    def test_m_suffix(self):
        assert extract_research_value("WERT: 750M") == Decimal("750000000")

    def test_german_decimal(self):
        assert extract_research_value("WERT: 27,65") == Decimal("27.65")

    def test_unit_mio_scales_value(self):
        text = "WERT: 1450\nEINHEIT: USD Mio.\nQUELLE: SEC 10-K\nZEITRAUM: FY2025"
        assert extract_research_value(text) == Decimal("1450000000")

    def test_unit_mrd_scales_value(self):
        text = "WERT: 139,9\nEINHEIT: EUR Mrd\nQUELLE: IR"
        assert extract_research_value(text) == Decimal("139900000000")

    def test_unit_million_english_scales_value(self):
        text = "WERT: 1450\nEINHEIT: USD million\nQUELLE: 10-K"
        assert extract_research_value(text) == Decimal("1450000000")

    def test_unit_suffix_not_doubled_when_wert_already_has_it(self):
        text = "WERT: 1.45 Mrd\nEINHEIT: USD Mrd\nQUELLE: x"
        assert extract_research_value(text) == Decimal("1450000000")

    def test_unit_plain_currency_no_scaling(self):
        text = "WERT: 1450000000\nEINHEIT: USD\nQUELLE: x"
        assert extract_research_value(text) == Decimal("1450000000")


# ---------------------------------------------------------------------------
# validate_claude_value
# ---------------------------------------------------------------------------

class TestValidateClaudeValue:
    def test_valid_stock_price(self):
        val = Decimal("150.00")
        assert validate_claude_value("stock_price", val) == val

    def test_negative_stock_price_rejected(self):
        assert validate_claude_value("stock_price", Decimal("-1")) is None

    def test_zero_stock_price_allowed(self):
        assert validate_claude_value("stock_price", Decimal("0")) == Decimal("0")

    def test_absurdly_large_stock_price_rejected(self):
        assert validate_claude_value("stock_price", Decimal("2000000")) is None

    def test_valid_market_cap(self):
        # Apple-size market cap
        val = Decimal("3000000000000")
        assert validate_claude_value("market_cap", val) == val

    def test_valid_sbc(self):
        assert validate_claude_value("sbc", Decimal("1_900_000_000")) == Decimal("1900000000")

    def test_sbc_absurd_rejected(self):
        assert validate_claude_value("sbc", Decimal("1e20")) is None

    def test_valid_net_income(self):
        val = Decimal("8000000000")
        assert validate_claude_value("net_income", val) == val

    def test_valid_eps_adj(self):
        val = Decimal("5.00")
        assert validate_claude_value("eps_adj", val) == val

    def test_unknown_key_passes_through(self):
        val = Decimal("999999999999999")
        assert validate_claude_value("some_unknown_key", val) == val

    def test_capex_negative_rejected(self):
        assert validate_claude_value("capex", Decimal("-1")) is None


# ---------------------------------------------------------------------------
# Yahoo provider _to_decimal edge cases
# ---------------------------------------------------------------------------

class TestYahooToDecimal:
    def setup_method(self):
        self.provider = YahooFinanceProvider()

    def test_none_returns_none(self):
        assert self.provider._to_decimal(None) is None

    def test_nan_float_returns_none(self):
        import math
        assert self.provider._to_decimal(float("nan")) is None

    def test_inf_float_returns_none(self):
        import math
        assert self.provider._to_decimal(float("inf")) is None

    def test_neg_inf_float_returns_none(self):
        import math
        assert self.provider._to_decimal(float("-inf")) is None

    def test_valid_int(self):
        assert self.provider._to_decimal(100) == Decimal("100")

    def test_valid_float(self):
        assert self.provider._to_decimal(3.14) == Decimal("3.14")

    def test_valid_string(self):
        assert self.provider._to_decimal("189.50") == Decimal("189.50")

    def test_invalid_string_returns_none(self):
        assert self.provider._to_decimal("not-a-number") is None

    def test_large_int(self):
        result = self.provider._to_decimal(3_000_000_000_000)
        assert result == Decimal("3000000000000")


# ---------------------------------------------------------------------------
# Yahoo provider _sanity_check
# ---------------------------------------------------------------------------

class TestYahooSanityCheck:
    def setup_method(self):
        self.provider = YahooFinanceProvider()

    def test_valid_stock_price(self):
        val = Decimal("150")
        assert self.provider._sanity_check("stock_price", val) == val

    def test_negative_stock_price_rejected(self):
        assert self.provider._sanity_check("stock_price", Decimal("-1")) is None

    def test_unknown_key_passes_through(self):
        val = Decimal("99999999999")
        assert self.provider._sanity_check("unknown_key", val) == val

    def test_net_income_valid(self):
        assert self.provider._sanity_check("net_income", Decimal("1000000000")) == Decimal("1000000000")

    def test_capex_negative_rejected(self):
        assert self.provider._sanity_check("capex", Decimal("-1")) is None


# ---------------------------------------------------------------------------
# Yahoo provider fetch with sanity checks applied
# ---------------------------------------------------------------------------

class TestYahooFetchSanityIntegration:
    def setup_method(self):
        self.provider = YahooFinanceProvider()

    def test_insane_stock_price_returns_none(self):
        mock_info = {"currentPrice": 5e11, "currency": "USD"}
        with patch.object(self.provider, "_get_info", return_value=mock_info):
            result = self.provider.fetch("TEST", "stock_price")
        assert result is None

    def test_normal_stock_price_passes(self):
        mock_info = {"currentPrice": 189.50, "currency": "USD"}
        with patch.object(self.provider, "_get_info", return_value=mock_info):
            result = self.provider.fetch("TEST", "stock_price")
        assert result is not None
        assert result.value == Decimal("189.50")

    def test_sales_snapshot_request_returns_none(self):
        """Sales is only fetched per-FY now; SNAPSHOT request yields None."""
        mock_info = {"totalRevenue": 100_000_000, "currency": "USD"}
        with patch.object(self.provider, "_get_info", return_value=mock_info):
            result = self.provider.fetch("TEST", "sales", period_type="SNAPSHOT")
        assert result is None


# ---------------------------------------------------------------------------
# VALUE_SANITY_CHECKS dict completeness
# ---------------------------------------------------------------------------

class TestSanityChecksDictCompleteness:
    def test_critical_keys_present(self):
        required = {
            "stock_price", "market_cap", "shares_outstanding",
            "debt", "cash", "net_income", "eps", "eps_adj",
            "op_cash_flow", "capex", "dividends", "sbc", "exchange_rate",
        }
        for key in required:
            assert key in VALUE_SANITY_CHECKS, f"Missing sanity check for {key}"

    def test_all_ranges_are_ordered(self):
        for key, (lo, hi) in VALUE_SANITY_CHECKS.items():
            assert lo <= hi, f"Sanity range for {key} is inverted: [{lo}, {hi}]"
