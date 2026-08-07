"""Zentrale Schreibpfad-Invarianten (app.values.persistence).

Jeder Writer (Refresh, Override, Research, Two-Stage, Skripte) muss durch
dieselben Invarianten: Sign-Normalisierung fuer ALWAYS_POSITIVE_KEYS und
Currency-Konflikt-Erkennung fuer CURRENCY_KEYS.
"""

from decimal import Decimal

from app.values.persistence import adjusted_is_protected, currency_conflict, normalize_sign


class TestNormalizeSign:
    def test_always_positive_key_flips_negative(self):
        assert normalize_sign("buyback_volume", Decimal("-3000")) == Decimal("3000")

    def test_always_positive_key_keeps_positive(self):
        assert normalize_sign("dividends", Decimal("200")) == Decimal("200")

    def test_net_debt_stays_negative(self):
        # net_debt kann Net-Cash sein — darf NICHT geflippt werden.
        assert normalize_sign("net_debt", Decimal("-50000000000")) == Decimal("-50000000000")

    def test_eps_can_be_negative(self):
        assert normalize_sign("eps_diluted", Decimal("-1.5")) == Decimal("-1.5")

    def test_none_passes_through(self):
        assert normalize_sign("buyback_volume", None) is None

    def test_zero_unchanged(self):
        assert normalize_sign("sbc", Decimal("0")) == Decimal("0")


class TestCurrencyConflict:
    def test_mismatch_on_currency_key(self):
        assert currency_conflict("revenue", "USD", "EUR") is True

    def test_match_no_conflict(self):
        assert currency_conflict("revenue", "USD", "USD") is False

    def test_missing_existing_no_conflict(self):
        assert currency_conflict("revenue", None, "EUR") is False

    def test_missing_new_no_conflict(self):
        assert currency_conflict("revenue", "USD", None) is False

    def test_non_currency_key_never_conflicts(self):
        assert currency_conflict("shares_outstanding", "USD", "EUR") is False


class TestAdjustedIsProtected:
    """Belegte Quellen (Manual + jede https-URL) schuetzen die Adjusted-
    Felder; unbelegt sind nur NULL und das Two-Stage-Format ('quote | url')."""

    def test_manual_protected(self):
        assert adjusted_is_protected("Manual") is True

    def test_sec_8k_url_protected(self):
        assert adjusted_is_protected("https://www.sec.gov/Archives/ex991.htm") is True

    def test_ir_press_release_url_protected(self):
        assert adjusted_is_protected("https://investor.example.com/press/q3.htm") is True

    def test_null_unprotected(self):
        assert adjusted_is_protected(None) is False

    def test_two_stage_quote_pipe_url_unprotected(self):
        assert adjusted_is_protected(
            "Adjusted net income was... | https://ir.example/pr"
        ) is False

    def test_http_plaintext_unprotected(self):
        # Nur https zaehlt als belegte Quelle.
        assert adjusted_is_protected("http://ir.example.com/pr") is False
