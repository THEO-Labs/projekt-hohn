"""Comprehensive tests for value parsing, sanity checks, and error handling."""
from decimal import Decimal
from unittest.mock import patch


from app.llm.claude import (
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

    # Suffixe (Mrd/B/M/...) werden hier NICHT skaliert — das macht
    # _apply_unit_scale in der Kette. _parse_numeric_string liefert nur
    # den numerischen Teil.
    def test_mrd_dot_returns_unscaled(self):
        assert _parse_numeric_string("14.77 Mrd") == Decimal("14.77")

    def test_mrd_comma_returns_unscaled_decimal(self):
        # Suffix darf die Dezimal-Komma-Heuristik nicht kippen (14,77 != 1477)
        assert _parse_numeric_string("14,77 Mrd") == Decimal("14.77")

    def test_b_suffix_returns_unscaled(self):
        assert _parse_numeric_string("14.77B") == Decimal("14.77")

    def test_m_suffix_with_thousands_returns_unscaled(self):
        assert _parse_numeric_string("14,770.65M") == Decimal("14770.65")

    def test_mio_german_returns_unscaled(self):
        assert _parse_numeric_string("14.770,65 Mio") == Decimal("14770.65")

    def test_negative_mrd_returns_unscaled(self):
        assert _parse_numeric_string("-1.5 Mrd") == Decimal("-1.5")

    def test_percent_stripped(self):
        assert _parse_numeric_string("4.38%") == Decimal("4.38")

    def test_simple_decimal(self):
        assert _parse_numeric_string("27.65") == Decimal("27.65")

    def test_empty_string(self):
        assert _parse_numeric_string("") is None

    def test_simple_negative(self):
        assert _parse_numeric_string("-1.5") == Decimal("-1.5")


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

    def test_m_suffix_with_us_thousands(self):
        assert extract_research_value("WERT: 14,770.65M") == Decimal("14770650000")

    def test_mio_german_format(self):
        assert extract_research_value("WERT: 14.770,65 Mio") == Decimal("14770650000")

    def test_mrd_comma_german_decimal(self):
        assert extract_research_value("WERT: 14,77 Mrd") == Decimal("14770000000")

    def test_billion_word(self):
        assert extract_research_value("WERT: 14.77 billion") == Decimal("14770000000")

    def test_t_suffix_trillion(self):
        assert extract_research_value("WERT: 2.5T") == Decimal("2500000000000")

    def test_k_suffix(self):
        assert extract_research_value("WERT: 500K") == Decimal("500000")

    def test_negative_mrd(self):
        assert extract_research_value("WERT: -1.5 Mrd") == Decimal("-1500000000")

    def test_prose_units_in_begruendung_do_not_scale(self):
        # Absolute WERT + Einheiten-Woerter nur in der Begruendungs-Prosa:
        # es darf NICHT skaliert werden (sonst 1e9-fach zu gross).
        text = (
            "WERT: 1450000000\n"
            "EINHEIT: USD\n"
            "BEGRUENDUNG: Das entspricht rund 1,45 Milliarden US-Dollar laut 10-K."
        )
        assert extract_research_value(text) == Decimal("1450000000")


# ---------------------------------------------------------------------------
# validate_claude_value
# ---------------------------------------------------------------------------

# validate_claude_value ist bewusst ein Pass-Through (Kunden-Anforderung:
# keine Range/Unit/YoY-Rejects). Diese Tests dokumentieren genau diesen
# Vertrag — schlaegt einer fehl, wurde die Validierung reaktiviert und die
# Caller-Erwartungen muessen neu geprueft werden.
class TestValidateClaudeValue:
    def test_valid_market_cap_passes_through(self):
        val = Decimal("3000000000000")
        assert validate_claude_value("market_cap", val) == val

    def test_negative_value_passes_through(self):
        assert validate_claude_value("market_cap", Decimal("-1")) == Decimal("-1")

    def test_absurdly_large_value_passes_through(self):
        assert validate_claude_value("sbc", Decimal("1e20")) == Decimal("1e20")

    def test_unknown_key_passes_through(self):
        val = Decimal("999999999999999")
        assert validate_claude_value("some_unknown_key", val) == val

    def test_net_debt_negative_passes_through(self):
        # net_debt CAN be negative (Net Cash Position).
        val = Decimal("-50000000000")
        assert validate_claude_value("net_debt", val) == val


# ---------------------------------------------------------------------------
# Yahoo provider _to_decimal edge cases
# ---------------------------------------------------------------------------

class TestYahooToDecimal:
    def setup_method(self):
        self.provider = YahooFinanceProvider()

    def test_none_returns_none(self):
        assert self.provider._to_decimal(None) is None

    def test_nan_float_returns_none(self):
        assert self.provider._to_decimal(float("nan")) is None

    def test_inf_float_returns_none(self):
        assert self.provider._to_decimal(float("inf")) is None

    def test_neg_inf_float_returns_none(self):
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

    def test_valid_market_cap(self):
        val = Decimal("1000000")
        assert self.provider._sanity_check("market_cap", val) == val

    def test_negative_market_cap_rejected(self):
        assert self.provider._sanity_check("market_cap", Decimal("-1")) is None

    def test_unknown_key_passes_through(self):
        val = Decimal("99999999999")
        assert self.provider._sanity_check("unknown_key", val) == val

    def test_net_income_valid(self):
        assert self.provider._sanity_check("net_income", Decimal("1000000000")) == Decimal("1000000000")

    def test_fcf_sanity_both_signs(self):
        assert self.provider._sanity_check("fcf", Decimal("1000000000")) == Decimal("1000000000")
        assert self.provider._sanity_check("fcf", Decimal("-1e17")) is None


# ---------------------------------------------------------------------------
# Yahoo provider fetch with sanity checks applied
# ---------------------------------------------------------------------------

class TestYahooFetchSanityIntegration:
    def setup_method(self):
        self.provider = YahooFinanceProvider()

    def test_insane_market_cap_rejected(self):
        mock_info = {"marketCap": 5e20, "currency": "USD"}
        with patch.object(self.provider, "_get_info", return_value=mock_info):
            result = self.provider.fetch("TEST", "market_cap")
        assert result is None

    def test_normal_market_cap_passes(self):
        mock_info = {"marketCap": 3_000_000_000_000, "currency": "USD"}
        with patch.object(self.provider, "_get_info", return_value=mock_info):
            result = self.provider.fetch("TEST", "market_cap")
        assert result is not None
        assert result.value == Decimal("3000000000000")

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
            "market_cap", "stock_price", "shares_outstanding",
            "sbc", "net_income", "fcf",
            "cash_and_equivalents", "marketable_securities_st", "marketable_securities_lt",
            "long_term_debt", "lease_liabilities",
            "buyback_volume", "dividends",
        }
        for key in required:
            assert key in VALUE_SANITY_CHECKS, f"Missing sanity check for {key}"

    def test_all_ranges_are_ordered(self):
        for key, (lo, hi) in VALUE_SANITY_CHECKS.items():
            assert lo <= hi, f"Sanity range for {key} is inverted: [{lo}, {hi}]"
