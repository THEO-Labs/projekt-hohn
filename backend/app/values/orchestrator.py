"""Neuer, schlanker Refresh-Kern (ersetzt den routes.py-Orchestrator).
Reihenfolge pro Firma: Stammdaten (Feed) -> EDGAR-Anker -> [Task6: Perplexity
Luecken + Konsens] -> engine.py Ableitung. Keine Gates.
Prioritaet pro Zelle: Manual > EDGAR(provider) > Perplexity > leer.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.values.always_current import ALWAYS_CURRENT_KEYS
from app.values.models import CompanyValue
from app.values.persistence import currency_conflict, normalize_sign
from app.values.provider_anchor import running_fy_year

logger = logging.getLogger(__name__)

# Schreib-Autoritaet pro Methode: hoehere (oder gleiche) Autoritaet darf
# ueberschreiben — gleiche Autoritaet, damit ein Refresh die eigenen Werte
# aktualisiert. Manuelle Overrides sind zusaetzlich hart ueber das
# manually_overridden-Flag geschuetzt.
_METHOD_RANK = {
    "manual": 3,
    "provider": 2,
    "market_feed": 2,
    "perplexity": 1,
    "perplexity_consensus": 1,
    "not_found": 0,
}


def _rank(method: str | None) -> int:
    return _METHOD_RANK.get(method or "", 0)


# Monetaere Kennzahlen (in Millionen). eps_diluted ist per-share und wird NICHT
# einheiten-normalisiert. Perplexity gibt Betraege gelegentlich in Milliarden
# statt Millionen zurueck (~1000x zu klein) — das korrigiert _unit_fix gegen
# den EDGAR-Anker.
_MONETARY_KEYS = frozenset({
    "revenue", "net_income", "ebitda", "operating_cash_flow", "fcf", "capex",
    "sbc", "buyback_volume", "dividends",
    "net_debt", "cash_and_equivalents", "st_investments", "st_debt", "lt_debt",
})


@dataclass(frozen=True)
class AnchorValue:
    value: Decimal
    source_name: str
    source_link: str | None
    currency: str | None


class ValueOrchestrator:
    def __init__(self, *, db, stammdaten_fetch, edgar_fetch, perplexity,
                 history_years: int = 2, on_phase=None):
        # history_years=2 -> Zielfenster [running_fy - 1, running_fy] = FY-1 + FY
        # (User-Entscheidung 17.08.2026). Das laufende FY liefert der Konsens,
        # FY-1 die berichteten Ist-Werte; FY-1 ist zugleich der Vorjahres-Anker
        # fuer das ni_growth/Delta-Net-Debt des laufenden FY.
        self.db = db
        self.stammdaten_fetch = stammdaten_fetch
        self.edgar_fetch = edgar_fetch
        self.perplexity = perplexity
        self.history_years = history_years
        # Optionaler Fortschritts-Callback on_phase(phase: str, label: str).
        # Erlaubt dem Aufrufer (refresh), echte Schritte anzuzeigen, ohne dass
        # der Orchestrator progress.py importiert (saubere Trennung).
        self.on_phase = on_phase

    def _emit(self, phase: str, label: str) -> None:
        if self.on_phase is not None:
            try:
                self.on_phase(phase, label)
            except Exception:  # noqa: BLE001 - Fortschritt darf nie den Lauf brechen
                pass

    # ---- helpers ---------------------------------------------------------
    def _existing(self, company_id, key, year, period_type="FY", is_forecast=False):
        # is_forecast gehoert zum Unique-Index: eine Slot kann Actual UND
        # Forecast fuehren. Immer den gemeinten Zwilling holen (sonst
        # MultipleResultsFound).
        return (self.db.query(CompanyValue)
                .filter_by(company_id=company_id, value_key=key,
                           period_year=year, period_type=period_type,
                           is_forecast=is_forecast)
                .one_or_none())

    def _writable(self, row, writer_method: str) -> bool:
        """Darf writer_method diese Zelle schreiben?
        - leere Zelle: ja
        - manuell ueberschrieben (Actual): nie
        - sonst: nur wenn writer_method mind. gleiche Autoritaet hat wie die
          bestehende Quelle (EDGAR aktualisiert EDGAR/Perplexity/leer;
          Perplexity aktualisiert nur Perplexity/leer, nie EDGAR)."""
        if row is None:
            return True
        if row.manually_overridden:
            return False
        return _rank(writer_method) >= _rank(row.primary_method)

    def _upsert(self, company_id, key, year, *, value, source_name, source_link,
                currency, primary_method, is_forecast=False, adjusted=None,
                period_type="FY"):
        row = self._existing(company_id, key, year, period_type, is_forecast)
        if not self._writable(row, primary_method):
            return
        value = normalize_sign(key, value)
        now = datetime.now(timezone.utc)
        if row is None:
            row = CompanyValue(id=uuid4(), company_id=company_id, value_key=key,
                               period_type=period_type, period_year=year)
            self.db.add(row)
        if currency_conflict(key, getattr(row, "currency", None), currency):
            logger.info("currency conflict %s FY%s: %s->%s (overwrite)",
                        key, year, row.currency, currency)
        row.numeric_value = value
        row.numeric_value_adjusted = (
            normalize_sign(key, Decimal(str(adjusted))) if adjusted is not None else None
        )
        row.source_name = source_name
        row.source_link = source_link
        row.currency = currency
        row.primary_method = primary_method
        row.is_forecast = is_forecast
        row.manually_overridden = False
        row.fetched_at = now
        row.last_refresh_attempt = now
        # Ein berichteter (Actual-)Wert weicht den Forecast-Zwilling desselben
        # Slots — auch einen manuellen: eine manuelle FORECAST-Zeile war nur ein
        # Schaetz-Override und wird von berichteten Zahlen ersetzt (Kontrakt wie
        # in provider_anchor.anchor_fy_with_provider). Manuelle ACTUAL-Zeilen
        # sind davon nicht betroffen (anderer Slot, oben durch _writable geschuetzt).
        if not is_forecast and year is not None:
            twin = self._existing(company_id, key, year, period_type, is_forecast=True)
            if twin is not None:
                self.db.delete(twin)

    # ---- flow ------------------------------------------------------------
    def target_years(self, company) -> list[int]:
        run = running_fy_year(company)
        return list(range(run - (self.history_years - 1), run + 1))

    def _apply_stammdaten(self, company):
        for key, (val, cur) in (self.stammdaten_fetch(company) or {}).items():
            if key not in ALWAYS_CURRENT_KEYS:
                continue
            # Stammdaten liegen als SNAPSHOT/period_year=None (so liest sie
            # _run_and_persist_calculations); NICHT als FY.
            self._upsert(company.id, key, None, value=val, source_name="Market Data Feed",
                         source_link=None, currency=cur, primary_method="market_feed",
                         period_type="SNAPSHOT")

    def _apply_edgar(self, company, years):
        for (key, year), av in (self.edgar_fetch(company, years) or {}).items():
            self._upsert(company.id, key, year, value=av.value, source_name=av.source_name,
                         source_link=av.source_link, currency=av.currency,
                         primary_method="provider")

    def run(self, company):
        years = self.target_years(company)
        self._emit("stammdaten", "Stammdaten (Kurs/MCap)")
        self._apply_stammdaten(company)
        self._emit("edgar", "EDGAR-XBRL-Werte")
        self._apply_edgar(company, years)
        # Flush VOR Perplexity: der Unit-Fix (_reference_magnitude) muss die
        # frisch geschriebenen EDGAR-Anker in der Transaktion sehen koennen —
        # ohne Flush (autoflush aus) ist die Referenz None und die Einheiten-
        # Angleichung greift nicht.
        self.db.flush()
        self._emit("perplexity", "Perplexity-Recherche")
        self._apply_perplexity(company, years)   # Task 6
        self.db.flush()
        self._emit("calculating", "Kennzahlen berechnen")
        self._derive_calculations(company, years)  # Task 6 (engine.py)
        self.db.flush()

    def run_stammdaten_only(self, company):
        """Daily-Numbers-Modus: nur die Live-Stammdaten (Feed) refreshen, KEIN
        EDGAR/Perplexity. Danach die berechneten Werte fuer den Snapshot und
        jedes bereits vorhandene FY-Jahr neu ableiten (die kursbasierten
        Multiples haengen am Live-Kurs)."""
        self._apply_stammdaten(company)
        self.db.flush()
        from app.values.routes import run_and_persist_calculations_for_years
        fy_years = [
            y for (y,) in self.db.query(CompanyValue.period_year)
            .filter(CompanyValue.company_id == company.id,
                    CompanyValue.period_type == "FY",
                    CompanyValue.period_year.isnot(None))
            .distinct().all()
        ]
        # None = Snapshot-Rechnung (market_cap_calc); plus jedes vorhandene FY.
        run_and_persist_calculations_for_years(self.db, company, [None] + sorted(fy_years))
        self.db.flush()

    def _reference_magnitude(self, company_id, key):
        """Betrag des juengsten berichteten EDGAR-Werts (provider) fuer key —
        als Einheiten-Referenz fuer die Perplexity-Skalierung."""
        row = (
            self.db.query(CompanyValue)
            .filter(
                CompanyValue.company_id == company_id,
                CompanyValue.value_key == key,
                CompanyValue.period_type == "FY",
                CompanyValue.is_forecast.is_(False),
                CompanyValue.primary_method == "provider",
                CompanyValue.numeric_value.isnot(None),
            )
            .order_by(CompanyValue.period_year.desc())
            .first()
        )
        return abs(row.numeric_value) if row and row.numeric_value else None

    def _unit_fix(self, company_id, key, value):
        """Einheiten-Angleichung (KEIN Plausibilitaets-Gate): das System speichert
        absolute Betraege (EDGAR-XBRL). Perplexity liefert teils in Tausend/
        Millionen/Milliarden. Weicht der Wert um einen 1000er-Faktor (>30x) vom
        vertrauten EDGAR-Anker derselben Kennzahl ab, wird er in 1000er-Schritten
        angeglichen. Echtes Wachstum ist nie >30x, daher eindeutig ein Einheiten-
        Fehler. Nur monetaere Keys; EPS/Ratios bleiben unangetastet."""
        if key not in _MONETARY_KEYS or value is None or value == 0:
            return value
        ref = self._reference_magnitude(company_id, key)
        logger.info("unit-fix probe %s: in=%s ref=%s", key, value, ref)  # TEMP debug
        if not ref:
            return value
        v = value
        guard = 0
        while abs(v) > 0 and ref / abs(v) > 30 and guard < 6:  # zu klein -> hoch
            v *= 1000
            guard += 1
        guard = 0
        while abs(v) > 0 and abs(v) / ref > 30 and guard < 6:  # zu gross -> runter
            v /= 1000
            guard += 1
        if v != value:
            logger.info("unit-fix %s: %s -> %s (ref=%s)", key, value, v, ref)
        return v

    def _missing_fundamental_keys(self, company_id, year, is_forecast=False):
        from app.values.schema_builder import fundamental_keys
        missing = []
        for k in fundamental_keys():
            # Ein geankerter/manueller ACTUAL blockiert auch die Forecast-Abfrage.
            actual = self._existing(company_id, k, year, is_forecast=False)
            if actual is not None and (actual.numeric_value is not None
                                       or actual.primary_method in ("manual", "provider")):
                continue
            if is_forecast:
                # Konsens ist zeitkritisch -> bei jedem Refresh NEU holen (aktuelle
                # Schaetzung + Einheiten-Fix greifen). Nur ein manueller Forecast-
                # Override wird nicht angefasst.
                frow = self._existing(company_id, k, year, is_forecast=True)
                if frow is not None and frow.manually_overridden:
                    continue
                missing.append(k)
                continue
            # Reported-Jahr: nur wirklich fehlende, nicht-provider/nicht-manuelle Zellen.
            if actual is None or (actual.numeric_value is None
                                  and actual.primary_method not in ("manual", "provider")):
                missing.append(k)
        return missing

    def _clear_forecast(self, company_id, year, keys):
        """Alte (nicht-manuelle) Forecast-Zellen der Keys leeren, bevor der
        Konsens neu geholt wird — sonst bleiben stale/falsch-skalierte Werte
        stehen, wenn das Modell einen Key diesmal nicht liefert."""
        (self.db.query(CompanyValue)
         .filter(CompanyValue.company_id == company_id,
                 CompanyValue.value_key.in_(list(keys)),
                 CompanyValue.period_type == "FY",
                 CompanyValue.period_year == year,
                 CompanyValue.is_forecast.is_(True),
                 CompanyValue.manually_overridden.is_(False))
         .delete(synchronize_session=False))
        self.db.flush()

    def _apply_perplexity(self, company, years):
        if self.perplexity is None:
            logger.warning("kein Perplexity-Client — Gap-Fill uebersprungen fuer %s",
                           getattr(company, "ticker", "?"))
            return
        from app.values.provider_anchor import _fy_is_closed
        currency = getattr(company, "currency", None) or "USD"
        run = years[-1]
        for year in years:
            forward = (year == run) and not _fy_is_closed(company, year)
            keys = self._missing_fundamental_keys(company.id, year, is_forecast=forward)
            if not keys:
                continue
            try:
                if forward:
                    self._clear_forecast(company.id, year, keys)
                    vals = self.perplexity.fetch_consensus(
                        company_name=company.name, ticker=company.ticker,
                        forward_year=year, keys=keys, currency=currency)
                    method, fc, src = "perplexity_consensus", True, "Perplexity"
                else:
                    vals = self.perplexity.fetch_period(
                        company_name=company.name, ticker=company.ticker,
                        fiscal_year=year, missing_keys=keys, currency=currency)
                    method, fc, src = "perplexity", False, "Perplexity"
            except Exception as e:
                logger.warning(
                    "perplexity fetch fehlgeschlagen %s FY%s: %s — Zellen bleiben offen",
                    getattr(company, "ticker", "?"), year, e,
                )
                continue
            for key, pv in vals.items():
                val = self._unit_fix(company.id, key, Decimal(str(pv.value)))
                adj = (self._unit_fix(company.id, key, Decimal(str(pv.adjusted)))
                       if pv.adjusted is not None else None)
                self._upsert(company.id, key, year, value=val,
                             source_name=src, source_link=pv.source_url, currency=currency,
                             primary_method=method, is_forecast=fc, adjusted=adj)

    def _derive_calculations(self, company, years):
        # Lazy import: run_and_persist_calculations_for_years lebt in routes.py.
        # Lazy vermeidet den Zyklus routes -> orchestrator -> routes.
        from app.values.routes import run_and_persist_calculations_for_years
        run_and_persist_calculations_for_years(self.db, company, years)
