"""EDGAR fetch_quarterly: YTD-Differenz fuer Cashflow-Keys, Instant-Facts
fuer Bilanz-Keys, Q4 aus FY-10-K minus 9M-YTD. Alles gegen Fixture-Facts,
kein Netz (_get_facts gemockt, CIK-Map vorbelegt)."""

from decimal import Decimal
from unittest.mock import patch

import pytest

from app.providers.edgar import EdgarProvider


@pytest.fixture
def provider():
    p = EdgarProvider()
    p._ticker_to_cik = {"MSFT": "0000789019"}
    return p


def _facts_with(concepts: dict[str, list[dict]]) -> dict:
    return {
        "facts": {
            "us-gaap": {
                name: {"units": {"USD": entries}} for name, entries in concepts.items()
            }
        }
    }


def _ytd(start: str, end: str, val: float, form: str = "10-Q",
         filed: str = "2025-01-01", fp: str | None = None,
         accn: str = "0000789019-25-000001") -> dict:
    e = {"start": start, "end": end, "val": val, "form": form,
         "filed": filed, "accn": accn}
    if fp:
        e["fp"] = fp
    return e


def _instant(end: str, val: float, form: str = "10-Q",
             filed: str = "2025-01-01",
             accn: str = "0000789019-25-000002") -> dict:
    return {"end": end, "val": val, "form": form, "filed": filed, "accn": accn}


# Kalenderjahr-Filer: YTD-Frames Q1-Q3 aus 10-Qs + FY aus dem 10-K.
CAPEX_YTD_ENTRIES = [
    _ytd("2025-01-01", "2025-03-31", 100e9, filed="2025-04-25"),
    _ytd("2025-01-01", "2025-06-30", 250e9, filed="2025-07-25"),
    _ytd("2025-01-01", "2025-09-30", 450e9, filed="2025-10-25"),
    _ytd("2025-01-01", "2025-12-31", 700e9, form="10-K", fp="FY", filed="2026-02-10"),
]

OCF_YTD_ENTRIES = [
    _ytd("2025-01-01", "2025-03-31", 300e9, filed="2025-04-25"),
    _ytd("2025-01-01", "2025-06-30", 650e9, filed="2025-07-25"),
    _ytd("2025-01-01", "2025-09-30", 1050e9, filed="2025-10-25"),
    _ytd("2025-01-01", "2025-12-31", 1500e9, form="10-K", fp="FY", filed="2026-02-10"),
]


def _fetch(provider, facts, key, quarter):
    with patch.object(provider, "_get_facts", return_value=facts):
        return provider.fetch_quarterly(
            "MSFT", key, 2025, quarter, fy_end_month=12, fy_end_day=31
        )


class TestYtdDiff:
    def test_q1_equals_ytd1(self, provider):
        # Q1-YTD-Frame (~90 Tage) matcht bereits die Standalone-Suche —
        # Wert identisch, Q1 = YTD(Q1) per Konstruktion.
        facts = _facts_with({"PaymentsToAcquirePropertyPlantAndEquipment": CAPEX_YTD_ENTRIES})
        res = _fetch(provider, facts, "capex", "Q1")
        assert res is not None
        assert res.value == Decimal("100e9")

    def test_q2_equals_ytd2_minus_ytd1(self, provider):
        facts = _facts_with({"PaymentsToAcquirePropertyPlantAndEquipment": CAPEX_YTD_ENTRIES})
        res = _fetch(provider, facts, "capex", "Q2")
        assert res is not None
        assert res.value == Decimal("150e9")
        assert "YTD-Differenz" in res.source_name

    def test_q3_equals_ytd3_minus_ytd2(self, provider):
        facts = _facts_with({"PaymentsToAcquirePropertyPlantAndEquipment": CAPEX_YTD_ENTRIES})
        res = _fetch(provider, facts, "capex", "Q3")
        assert res is not None
        assert res.value == Decimal("200e9")

    def test_q4_equals_fy_minus_ytd3(self, provider):
        facts = _facts_with({"PaymentsToAcquirePropertyPlantAndEquipment": CAPEX_YTD_ENTRIES})
        res = _fetch(provider, facts, "capex", "Q4")
        assert res is not None
        assert res.value == Decimal("250e9")
        assert "FY minus 9M-YTD" in res.source_name
        assert "10-K" in res.source_name

    def test_negative_diff_returns_none(self, provider):
        # YTD2 < YTD1 (Restatement-Artefakt) -> kein Muell liefern.
        entries = [
            _ytd("2025-01-01", "2025-03-31", 200e9, filed="2025-04-25"),
            _ytd("2025-01-01", "2025-06-30", 150e9, filed="2025-07-25"),
        ]
        facts = _facts_with({"PaymentsToAcquirePropertyPlantAndEquipment": entries})
        assert _fetch(provider, facts, "capex", "Q2") is None

    def test_standalone_wins_over_ytd_diff(self, provider):
        # Standalone-3M-Frame existiert zusaetzlich -> hat Vorrang.
        entries = CAPEX_YTD_ENTRIES + [
            _ytd("2025-04-01", "2025-06-30", 160e9, filed="2025-07-25"),
        ]
        facts = _facts_with({"PaymentsToAcquirePropertyPlantAndEquipment": entries})
        res = _fetch(provider, facts, "capex", "Q2")
        assert res is not None
        assert res.value == Decimal("160e9")
        assert "YTD-Differenz" not in res.source_name

    def test_q4_without_fy_entry_returns_none(self, provider):
        entries = [e for e in CAPEX_YTD_ENTRIES if e["form"] != "10-K"]
        facts = _facts_with({"PaymentsToAcquirePropertyPlantAndEquipment": entries})
        assert _fetch(provider, facts, "capex", "Q4") is None

    def test_q4_rejects_non_annual_fy_entry(self, provider):
        # FY-Seite des Q4-Diffs braucht echte Jahres-Duration (350-380 Tage):
        # ein 6M-Frame im 10-K darf nicht als FY-Basis dienen.
        entries = [
            _ytd("2025-01-01", "2025-09-30", 450e9, filed="2025-10-25"),
            _ytd("2025-07-01", "2025-12-31", 400e9, form="10-K", fp="FY",
                 filed="2026-02-10"),
        ]
        facts = _facts_with({"PaymentsToAcquirePropertyPlantAndEquipment": entries})
        assert _fetch(provider, facts, "capex", "Q4") is None

    def test_q2_missing_ytd1_returns_none(self, provider):
        # YTD2 vorhanden, YTD1 fehlt -> kein Diff moeglich.
        entries = [
            _ytd("2025-01-01", "2025-06-30", 250e9, filed="2025-07-25"),
        ]
        facts = _facts_with({"PaymentsToAcquirePropertyPlantAndEquipment": entries})
        assert _fetch(provider, facts, "capex", "Q2") is None

    def test_q2_diff_with_june_fy(self, provider):
        # Juni-FY (MSFT-Muster): FY2026 = Jul 2025 - Jun 2026, YTD-Frames
        # starten am 2025-07-01. Q2 endet 2025-12-31.
        entries = [
            _ytd("2025-07-01", "2025-09-30", 100e9, filed="2025-10-25"),
            _ytd("2025-07-01", "2025-12-31", 260e9, filed="2026-01-25"),
        ]
        facts = _facts_with({"PaymentsToAcquirePropertyPlantAndEquipment": entries})
        with patch.object(provider, "_get_facts", return_value=facts):
            res = provider.fetch_quarterly(
                "MSFT", "capex", 2026, "Q2", fy_end_month=6, fy_end_day=30
            )
        assert res is not None
        assert res.value == Decimal("160e9")
        assert "YTD-Differenz" in res.source_name


class TestFcfQuarterly:
    def test_fcf_quarter_is_ocf_minus_abs_capex(self, provider):
        facts = _facts_with({
            "PaymentsToAcquirePropertyPlantAndEquipment": CAPEX_YTD_ENTRIES,
            "NetCashProvidedByUsedInOperatingActivities": OCF_YTD_ENTRIES,
        })
        res = _fetch(provider, facts, "fcf", "Q2")
        assert res is not None
        # OCF Q2 = 650 - 300 = 350; CapEx Q2 = 250 - 100 = 150; FCF = 200
        assert res.value == Decimal("200e9")
        assert "FCF = OCF - CapEx" in res.source_name

    def test_fcf_missing_component_returns_none(self, provider):
        facts = _facts_with({
            "NetCashProvidedByUsedInOperatingActivities": OCF_YTD_ENTRIES,
        })
        assert _fetch(provider, facts, "fcf", "Q2") is None

    def test_fcf_q4_from_fy_minus_ytd3(self, provider):
        facts = _facts_with({
            "PaymentsToAcquirePropertyPlantAndEquipment": CAPEX_YTD_ENTRIES,
            "NetCashProvidedByUsedInOperatingActivities": OCF_YTD_ENTRIES,
        })
        res = _fetch(provider, facts, "fcf", "Q4")
        assert res is not None
        # OCF Q4 = 1500 - 1050 = 450; CapEx Q4 = 700 - 450 = 250; FCF = 200
        assert res.value == Decimal("200e9")


class TestBalanceInstant:
    def test_cash_from_instant_fact(self, provider):
        facts = _facts_with({
            "CashAndCashEquivalentsAtCarryingValue": [
                _instant("2025-03-31", 80e9, filed="2025-04-25"),
            ]
        })
        res = _fetch(provider, facts, "cash_and_equivalents", "Q1")
        assert res is not None
        assert res.value == Decimal("80e9")
        assert "Bilanz-Stichtag" in res.source_name

    def test_cash_q4_from_10k_instant(self, provider):
        facts = _facts_with({
            "CashAndCashEquivalentsAtCarryingValue": [
                _instant("2025-12-31", 95e9, form="10-K", filed="2026-02-10"),
            ]
        })
        res = _fetch(provider, facts, "cash_and_equivalents", "Q4")
        assert res is not None
        assert res.value == Decimal("95e9")
        assert "10-K" in res.source_name

    def test_no_instant_entry_returns_none(self, provider):
        # Nur Duration-Eintraege (mit start) -> Instant-Suche liefert nichts.
        facts = _facts_with({
            "CashAndCashEquivalentsAtCarryingValue": [
                _ytd("2025-01-01", "2025-03-31", 80e9),
            ]
        })
        assert _fetch(provider, facts, "cash_and_equivalents", "Q1") is None


class TestOtherKeysUnchanged:
    def test_revenue_q4_stays_none(self, provider):
        # Income-Statement-Keys: Q4 hat keine separaten 3M-Frames -> None.
        facts = _facts_with({
            "Revenues": [
                _ytd("2025-10-01", "2025-12-31", 70e9, form="10-K", filed="2026-02-10"),
            ]
        })
        assert _fetch(provider, facts, "revenue", "Q4") is None

    def test_revenue_standalone_still_works(self, provider):
        facts = _facts_with({
            "Revenues": [
                _ytd("2025-04-01", "2025-06-30", 65e9, filed="2025-07-25"),
            ]
        })
        res = _fetch(provider, facts, "revenue", "Q2")
        assert res is not None
        assert res.value == Decimal("65e9")
