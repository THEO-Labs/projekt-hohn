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
    "derived": 2,   # deterministische Ableitung aus EDGAR-Komponenten (net_debt)
    "perplexity": 1,
    "perplexity_consensus": 1,
    # Voll-FY-Schaetzung fuer ein gerade-gestartetes Jahr OHNE ein einziges
    # berichtetes Quartal — schwaechster Anker, in der UI eigens markiert.
    "estimate_unanchored": 1,
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

# Flow-Kennzahlen (ueber Quartale summierbar): laufendes FY = Q1+Q2+Q3+Q4.
_FLOW_KEYS = frozenset({
    "revenue", "net_income", "ebitda", "operating_cash_flow", "fcf", "capex",
    "sbc", "buyback_volume", "dividends", "eps_diluted",
})
# Bilanz-Kennzahlen (Stichtag): laufendes FY = letztes berichtetes Quartal
# (Carry-Forward). net_debt wird NICHT geschaetzt, sondern abgeleitet.
_BALANCE_KEYS = frozenset({
    "cash_and_equivalents", "st_investments", "st_debt", "lt_debt",
})

# Diese Keys NIE vom LLM holen: Schulden-Stichtagswerte werden vom LLM
# halluziniert (Natera: 362M/694M statt real 80M Kreditlinie). Sie kommen
# ausschliesslich aus EDGAR/Filing-XBRL (provider); fehlen sie, ist die Firma
# faktisch schuldenfrei -> net_debt = Netto-Cash (korrekt).
_LLM_EXCLUDED_KEYS = frozenset({"st_debt", "lt_debt"})


@dataclass(frozen=True)
class AnchorValue:
    value: Decimal
    source_name: str
    source_link: str | None
    currency: str | None


class ValueOrchestrator:
    def __init__(self, *, db, stammdaten_fetch, edgar_fetch, perplexity,
                 history_years: int = 2, on_phase=None, filing_provider=None):
        # history_years=2 -> Zielfenster [running_fy - 1, running_fy] = FY-1 + FY
        # (User-Entscheidung 17.08.2026). Das laufende FY liefert der Konsens,
        # FY-1 die berichteten Ist-Werte; FY-1 ist zugleich der Vorjahres-Anker
        # fuer das ni_growth/Delta-Net-Debt des laufenden FY.
        self.db = db
        self.stammdaten_fetch = stammdaten_fetch
        self.edgar_fetch = edgar_fetch
        self.perplexity = perplexity
        self.history_years = history_years
        # Filing-XBRL-Provider (exakte Werte aus dem konkreten 10-Q/10-K, wenn
        # companyfacts noch hinterherhinkt). Lazy erzeugt, siehe _filing.
        self._filing_provider = filing_provider
        self._filing_provider_built = filing_provider is not None
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

    def _apply_edgar_quarters(self, company, years):
        """EDGAR-XBRL-Quartale (Q1-Q4 Actuals). Muss VOR der Perplexity-Q4-
        Schaetzung + FY-aus-Quartalen laufen."""
        from app.values.provider_anchor import anchor_quarters_with_provider
        try:
            anchor_quarters_with_provider(self.db, company, years)
        except Exception as e:
            logger.warning("quarter anchor failed for %s: %s",
                           getattr(company, "ticker", "?"), e)

    def run(self, company):
        years = self.target_years(company)
        self._emit("stammdaten", "Stammdaten (Kurs/MCap)")
        self._apply_stammdaten(company)
        self._emit("edgar", "EDGAR-XBRL-Werte")
        self._apply_edgar(company, years)
        self._emit("quarters", "EDGAR-Quartale")
        self._apply_edgar_quarters(company, years)
        # Flush VOR Perplexity: der Unit-Fix (_reference_magnitude) + die
        # FY-aus-Quartalen-Logik muessen die frisch geschriebenen EDGAR-Anker
        # (FY + Q1-Q3) in der Transaktion sehen (autoflush aus).
        self.db.flush()
        self._emit("perplexity", "Quellen & Schätzung")
        self._apply_perplexity(company, years)
        self.db.flush()
        self._emit("derive", "Ableitungen (FY, Q4, Net Debt)")
        self._finalize_estimates(company, years)
        self.db.flush()
        self._emit("adjusted", "Adjusted / Non-GAAP")
        self._apply_adjusted(company, years)
        self.db.flush()
        self._emit("calculating", "Kennzahlen berechnen")
        self._derive_calculations(company, years)
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
            if k in _LLM_EXCLUDED_KEYS:
                continue  # Schulden nur aus EDGAR/Filing, nie vom LLM
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

    def _clear_forecast_slots(self, company_id, year, keys, period_types):
        """Alte (nicht-manuelle) Forecast-Zellen der Keys/Perioden leeren, bevor
        neu geschaetzt wird — sonst bleiben stale Werte stehen."""
        (self.db.query(CompanyValue)
         .filter(CompanyValue.company_id == company_id,
                 CompanyValue.value_key.in_(list(keys)),
                 CompanyValue.period_type.in_(list(period_types)),
                 CompanyValue.period_year == year,
                 CompanyValue.is_forecast.is_(True),
                 CompanyValue.manually_overridden.is_(False))
         .delete(synchronize_session=False))
        self.db.flush()

    def _apply_perplexity(self, company, years):
        if self.perplexity is None:
            logger.warning("kein Perplexity-Client — Schaetzung uebersprungen fuer %s",
                           getattr(company, "ticker", "?"))
            return
        from app.values.provider_anchor import _fy_is_closed
        currency = getattr(company, "currency", None) or "USD"
        for year in years:
            # Abgeschlossenes FY: berichtete FY-Luecken via Perplexity holen
            # (as-reported). Fuellt fuer voll gefilte Jahre alle Restluecken.
            if _fy_is_closed(company, year):
                self._fill_reported_gaps(company, year, currency)
            # Jahr ohne vollstaendigen EDGAR-Jahreswert (laufendes FY ODER gerade
            # beendetes FY mit noch nicht gefiledem 10-K): berichtete Quartale
            # exakt bridgen + Bilanz-Carry. Die FY-Schaetzung (Guidance/Konsens,
            # an die Quartale grundiert) folgt in _finalize.
            if self._needs_estimate_completion(company, year):
                self._estimate_running_fy(company, year, currency)
            # EDGAR-Luecken in berichteten Quartalen exakt fuellen (z.B. eps/
            # st_investments, wo EDGAR das Concept nicht hat).
            self._fill_quarter_gaps(company, year, currency)

    def _fill_quarter_gaps(self, company, year, currency):
        """Fuellt in berichteten Quartalen die Keys, die EDGAR nicht liefert
        (fehlendes XBRL-Concept, z.B. Visa eps/st_investments), exakt aus den
        Tabellen via Perplexity. primary_method='perplexity' (Actual) -> der
        EDGAR-Anker ueberschreibt, sobald das Concept doch kommt."""
        from app.values.schema_builder import fundamental_keys
        all_keys = fundamental_keys()
        for q in ("Q1", "Q2", "Q3", "Q4"):
            if not self._has_reported(company.id, "revenue", year, q):
                continue  # Quartal (noch) nicht berichtet
            missing = [k for k in all_keys if k not in _LLM_EXCLUDED_KEYS and not self._has_reported(company.id, k, year, q)]
            if not missing:
                continue
            try:
                vals = self.perplexity.fetch_quarter_reported(
                    company_name=company.name, ticker=company.ticker,
                    fiscal_year=year, quarter=q, keys=missing, currency=currency)
            except Exception as e:
                logger.warning("perplexity quarter-gap %s %s FY%s: %s",
                               getattr(company, "ticker", "?"), q, year, e)
                continue
            for key, pv in vals.items():
                val = self._unit_fix(company.id, key, Decimal(str(pv.value)))
                self._upsert(company.id, key, year, period_type=q, value=val,
                             source_name="Quelle", source_link=pv.source_url,
                             currency=currency, primary_method="perplexity", is_forecast=False)
        self.db.flush()

    def _apply_adjusted(self, company, years):
        """Firmen-definierte adjusted/Non-GAAP-Werte fuer berichtete Jahre holen
        und als numeric_value_adjusted auf die bestehenden GAAP-Zeilen setzen
        (GAAP-Wert bleibt unangetastet)."""
        if self.perplexity is None:
            return
        from app.values.metric_definitions import ADJUSTED_KEYS
        from app.values.provider_anchor import _fy_is_closed
        keys = sorted(ADJUSTED_KEYS)
        currency = getattr(company, "currency", None) or "USD"
        for year in years:
            if not _fy_is_closed(company, year):
                continue  # nur berichtete (abgeschlossene) Jahre
            try:
                vals = self.perplexity.fetch_adjusted(
                    company_name=company.name, ticker=company.ticker,
                    fiscal_year=year, keys=keys, currency=currency)
            except Exception as e:
                logger.warning("perplexity adjusted %s FY%s: %s",
                               getattr(company, "ticker", "?"), year, e)
                continue
            for key, pv in vals.items():
                row = self._existing(company.id, key, year, is_forecast=False)
                if row is None or row.numeric_value is None:
                    continue
                row.numeric_value_adjusted = self._unit_fix(
                    company.id, key, Decimal(str(pv.value)))
        self.db.flush()

    def _fill_reported_gaps(self, company, year, currency):
        keys = self._missing_fundamental_keys(company.id, year, is_forecast=False)
        if not keys:
            return
        try:
            vals = self.perplexity.fetch_period(
                company_name=company.name, ticker=company.ticker,
                fiscal_year=year, missing_keys=keys, currency=currency)
        except Exception as e:
            logger.warning("perplexity fetch_period fehlgeschlagen %s FY%s: %s",
                           getattr(company, "ticker", "?"), year, e)
            return
        for key, pv in vals.items():
            val = self._unit_fix(company.id, key, Decimal(str(pv.value)))
            adj = (self._unit_fix(company.id, key, Decimal(str(pv.adjusted)))
                   if pv.adjusted is not None else None)
            self._upsert(company.id, key, year, value=val, source_name="Quelle",
                         source_link=pv.source_url, currency=currency,
                         primary_method="perplexity", is_forecast=False, adjusted=adj)

    def _estimate_running_fy(self, company, year, currency):
        """Ein noch nicht vollstaendig berichtetes FY vorbereiten: alte Forecasts
        leeren, berichtete Quartale exakt aus der Filing-XBRL holen (Bridge),
        Bilanz-Stichtag uebernehmen. Die eigentliche FY-Schaetzung (Guidance /
        Analysten-Konsens, an die berichteten Quartale grundiert) macht danach
        _running_fy_from_quarters — bewusst KEINE mechanische Hochrechnung."""
        flow_keys = sorted(_FLOW_KEYS)
        self._clear_forecast_slots(company.id, year, flow_keys + list(_BALANCE_KEYS) + ["net_debt"],
                                   ("Q1", "Q2", "Q3", "Q4", "FY"))
        # Bridge: berichtete Quartale, die die aggregierte companyfacts-API noch
        # nicht hat (Filing-Lag), exakt aus der XBRL-Instanz des Filings holen.
        self._bridge_missing_quarters(company, year, currency)
        self._carry_forward_balances(company, year, currency)

    def _has_reported(self, company_id, key, year, period_type):
        r = self._existing(company_id, key, year, period_type=period_type, is_forecast=False)
        return r is not None and r.numeric_value is not None

    def _filing(self):
        """Lazy: Filing-XBRL-Provider erst bei Bedarf bauen (ein HTTP-Client)."""
        if not self._filing_provider_built:
            try:
                from app.providers.edgar_filing import EdgarFilingProvider
                self._filing_provider = EdgarFilingProvider()
            except Exception as e:  # noqa: BLE001
                logger.warning("EdgarFilingProvider nicht verfuegbar: %s", e)
                self._filing_provider = None
            self._filing_provider_built = True
        return self._filing_provider

    def _quarter_prior_sum(self, company_id, key, year, quarter):
        """Summe der berichteten Standalone-Vorquartale fuer key (fuer die
        YTD->Quartal-Differenz). Rueckgabe (summe, alle_vorhanden)."""
        priors = {"Q1": [], "Q2": ["Q1"], "Q3": ["Q1", "Q2"], "Q4": ["Q1", "Q2", "Q3"]}
        total = Decimal("0")
        for pq in priors.get(quarter, []):
            r = self._existing(company_id, key, year, period_type=pq, is_forecast=False)
            if r is None or r.numeric_value is None:
                return total, False
            total += abs(r.numeric_value)
        return total, True

    def _bridge_from_filing(self, company, year, quarter, currency):
        """Exakte Quartalswerte aus der XBRL-Instanz des konkreten 10-Q/10-K
        (companyfacts hinkt frisch eingereichten Filings hinterher). Schreibt
        Income-Statement (3M direkt), Cashflow (YTD-Differenz gegen Vorquartale)
        und Bilanz (Instant). primary_method='provider' (SEC-XBRL, autoritativ)
        -> Perplexity ueberschreibt das nicht. Rueckgabe: Menge gefuellter Keys."""
        prov = self._filing()
        if prov is None:
            return set()
        fym = getattr(company, "fiscal_year_end_month", None)
        fyd = getattr(company, "fiscal_year_end_day", None)
        try:
            fq = prov.fetch_quarter(ticker=company.ticker, fiscal_year=year,
                                    quarter=quarter, fy_end_month=fym, fy_end_day=fyd)
        except Exception as e:  # noqa: BLE001
            logger.warning("filing-xbrl %s %s FY%s: %s",
                           getattr(company, "ticker", "?"), quarter, year, e)
            return set()
        if fq is None:
            return set()
        src = fq.source_url
        filled: set[str] = set()

        def _put(key, value):
            self._upsert(company.id, key, year, period_type=quarter,
                         value=self._unit_fix(company.id, key, value),
                         source_name="SEC EDGAR (Filing-XBRL)", source_link=src,
                         currency=currency, primary_method="provider", is_forecast=False)
            filled.add(key)

        # Income-Statement: direkter 3-Monats-Quartalswert.
        for key, val in fq.quarter_values.items():
            _put(key, val)
        # Cashflow: Quartal = YTD - Summe(Vorquartale). Nur wenn alle
        # Vorquartale berichtet sind (sonst waere die Differenz falsch).
        for key, ytd in fq.ytd_values.items():
            prior, complete = self._quarter_prior_sum(company.id, key, year, quarter)
            if not complete:
                continue
            q_val = ytd - prior
            if q_val < 0:  # Restatement-Artefakt -> verwerfen
                continue
            _put(key, q_val)
        # eps exakt aus NI/diluted-shares, wenn der Filer beides dimensionslos taggt.
        if fq.diluted_shares and fq.diluted_shares > 0 and "net_income" in fq.quarter_values:
            eps_q = fq.quarter_values["net_income"] / fq.diluted_shares
            self._upsert(company.id, "eps_diluted", year, period_type=quarter,
                         value=eps_q, source_name="SEC EDGAR (Filing-XBRL)",
                         source_link=src, currency=currency, primary_method="provider",
                         is_forecast=False)
            filled.add("eps_diluted")
        # Bilanz: Instant am Quartalsende (fuellt st_debt/st_investments, die
        # EDGAR-companyfacts pro Quartal nicht liefert) -> _carry_forward_balances
        # nimmt diesen Stichtag statt des Vorjahres-Proxys.
        for key, val in fq.balance_values.items():
            _put(key, val)
        return filled

    def _bridge_missing_quarters(self, company, year, currency):
        """Berichtete Quartale, deren 10-Q-XBRL noch nicht in der aggregierten
        companyfacts-API ist (Filing-Lag). Primaerquelle: die XBRL-Instanz des
        konkreten Filings (exakt). Fallback fuer Keys, die das Filing nicht
        liefert: Perplexity aus dem Earnings-Release."""
        from datetime import date, timedelta

        from app.values.detail_page import quarter_end_date
        from app.values.schema_builder import fundamental_keys
        today = date.today()
        lag = timedelta(days=35)  # Earnings ~3-5 Wochen nach Quartalsende
        fym = getattr(company, "fiscal_year_end_month", None)
        fyd = getattr(company, "fiscal_year_end_day", None)
        all_keys = fundamental_keys()
        # Q4 einbeziehen: ein gerade beendetes FY, dessen 10-K noch nicht gefiled
        # ist (companyfacts leer), aber dessen Q4-Earnings-Release/8-K schon
        # draussen ist -> exakt bridgen. Die Lag-Pruefung schuetzt vor noch nicht
        # berichteten Quartalen.
        for q in ("Q1", "Q2", "Q3", "Q4"):
            qend = quarter_end_date(year, q, fym, fyd)
            if qend is None or qend + lag > today:
                continue  # Quartal (noch) nicht berichtet
            rev = self._existing(company.id, "revenue", year, period_type=q, is_forecast=False)
            if rev is not None and rev.numeric_value is not None and rev.primary_method == "provider":
                continue  # companyfacts hat das Quartal schon exakt (kein Bridge noetig)
            # Sonst: Quartal ist berichtet, aber noch nicht (oder nur via alter
            # Perplexity-Bridge) da -> exakte Filing-XBRL holen (ueberschreibt
            # stale Perplexity-Werte, primary_method=provider).
            # 1) Exakte Werte aus der Filing-XBRL-Instanz.
            self._bridge_from_filing(company, year, q, currency)
            # 2) Perplexity-Fallback nur fuer weiterhin fehlende Keys.
            missing = [k for k in all_keys if k not in _LLM_EXCLUDED_KEYS and not self._has_reported(company.id, k, year, q)]
            if not missing or self.perplexity is None:
                continue
            try:
                vals = self.perplexity.fetch_quarter_reported(
                    company_name=company.name, ticker=company.ticker,
                    fiscal_year=year, quarter=q, keys=missing, currency=currency)
            except Exception as e:
                logger.warning("perplexity bridge %s %s FY%s: %s",
                               getattr(company, "ticker", "?"), q, year, e)
                continue
            for key, pv in vals.items():
                val = self._unit_fix(company.id, key, Decimal(str(pv.value)))
                self._upsert(company.id, key, year, period_type=q, value=val,
                             source_name="Quelle", source_link=pv.source_url,
                             currency=currency, primary_method="perplexity", is_forecast=False)
        self.db.flush()

    def _reported_quarters(self, company_id, key, year):
        """dict {Q: numeric_value} der berichteten (is_forecast=False) Quartale."""
        out = {}
        for q in ("Q1", "Q2", "Q3", "Q4"):
            r = self._existing(company_id, key, year, period_type=q, is_forecast=False)
            if r is not None and r.numeric_value is not None:
                out[q] = r.numeric_value
        return out

    def _reported_actuals_context(self, company_id, year, keys):
        """Kompakter Kontext fuers Grounding der Konsens/Guidance-Abfrage: die
        bereits BERICHTETEN Quartale dieses FY + der letzte bekannte Vorjahres-
        Jahreswert (GAAP-Groessenordnungs-Anker). Verhindert Ausreisser und die
        GAAP/Non-GAAP-Verwechslung. Werte in Mio, lesbar fuers LLM."""
        def fmt(r, key):
            if r is None or r.numeric_value is None:
                return None
            if key == "eps_diluted":
                return f"{key}={r.numeric_value:.2f}/sh"
            return f"{key}={r.numeric_value / 1_000_000:.0f}m"
        lines = []
        for q in ("Q1", "Q2", "Q3", "Q4"):
            parts = [p for key in keys
                     if (p := fmt(self._existing(company_id, key, year, period_type=q,
                                                 is_forecast=False), key))]
            if parts:
                lines.append(f"FY{year} {q} actual: " + ", ".join(parts))
        # Vorjahres-Jahreswert als GAAP-Anker (Actual bevorzugt, sonst Schaetzung).
        prior_parts = []
        for key in keys:
            r = (self._existing(company_id, key, year - 1, period_type="FY", is_forecast=False)
                 or self._existing(company_id, key, year - 1, period_type="FY", is_forecast=True))
            p = fmt(r, key)
            if p:
                prior_parts.append(p)
        if prior_parts:
            lines.append(f"FY{year - 1} full-year (GAAP anchor): " + ", ".join(prior_parts))
        return " | ".join(lines)

    def _has_full_fy_anchor(self, company_id, year):
        """True, wenn das Jahr wirklich BERICHTET ist: irgendeine Kern-Kennzahl
        hat einen EDGAR-Provider-FY-Anker (dann ist der 10-K gefiled und EDGAR
        hat die vollen Statements), ODER alle vier Quartale liegen als Actuals
        vor. Nur dann ist keine Schaetz-Vervollstaendigung noetig."""
        for key in ("revenue", "net_income", "operating_cash_flow"):
            fy = self._existing(company_id, key, year, period_type="FY", is_forecast=False)
            if fy is not None and fy.numeric_value is not None and _rank(fy.primary_method) >= 2:
                return True
        return len(self._reported_quarters(company_id, "revenue", year)) == 4

    def _needs_estimate_completion(self, company, year):
        """Ein Jahr braucht Schaetz-Vervollstaendigung (berichtete Quartale +
        Guidance/Konsens), wenn EDGAR keinen vollstaendigen berichteten
        Jahreswert liefert — deckt das laufende FY UND ein gerade beendetes FY
        ab, dessen 10-K noch nicht gefiled ist (z.B. Intuit im August)."""
        return not self._has_full_fy_anchor(company.id, year)

    def _carry_forward_balances(self, company, year, currency):
        """Bilanz-Keys fuers laufende FY: Jahresend-Stichtag ~ letztes berichtetes
        Quartal (Carry-Forward). Deterministisch, keine Schaetzung."""
        for key in _BALANCE_KEYS:
            fy_actual = self._existing(company.id, key, year, is_forecast=False)
            if fy_actual is not None and fy_actual.numeric_value is not None:
                continue
            latest = None
            for q in ("Q4", "Q3", "Q2", "Q1"):
                r = self._existing(company.id, key, year, period_type=q, is_forecast=False)
                if r is not None and r.numeric_value is not None:
                    latest = r
                    break
            if latest is None:
                # Fallback: letzter Jahresschluss (FY-1) als Proxy (Bilanz aendert
                # sich langsam) — deckt Keys ab, die EDGAR nur jaehrlich liefert
                # (st_debt). Auch ein Forecast-Vorjahr (selbst ein Carry-Forward)
                # ist als Proxy besser als leer — z.B. gerade-gestartetes FY ohne
                # eigene Quartale, dessen Vorjahres-Bilanz selbst gebridged wurde.
                prev = (self._existing(company.id, key, year - 1, period_type="FY", is_forecast=False)
                        or self._existing(company.id, key, year - 1, period_type="FY", is_forecast=True))
                if prev is not None and prev.numeric_value is not None:
                    latest = prev
            if latest is None:
                continue
            self._upsert(company.id, key, year, period_type="FY", value=latest.numeric_value,
                         source_name="Carry-Forward (letzter Stichtag)", source_link=None,
                         currency=latest.currency or currency,
                         primary_method="perplexity_consensus", is_forecast=True)

    def _finalize_estimates(self, company, years):
        """Nach EDGAR + Perplexity: vollstaendig berichtete Jahre Q4=FY−Q1-Q3;
        noch offene Jahre FY = Q1..Q4 (fehlende Quartale Guidance/Konsens);
        net_debt deterministisch aus Komponenten."""
        from app.values.quarter_residual import derive_q4_from_fy_residual
        currency = getattr(company, "currency", None) or "USD"
        # Vollstaendig berichtete Jahre (EDGAR-FY-Anker): Q4 = FY − 9M-YTD.
        anchored = [y for y in years if self._has_full_fy_anchor(company.id, y)]
        if anchored:
            derive_q4_from_fy_residual(self.db, company, anchored)
        # Offene Jahre (laufendes FY oder gerade beendetes ohne 10-K): FY aus
        # berichteten Quartalen + Guidance/Konsens fuer den Rest.
        for y in years:
            if self._needs_estimate_completion(company, y):
                self._running_fy_from_quarters(company, y, currency)
        self.db.flush()
        self._repair_net_income_gaap(company, years)  # KI-NI gegen GAAP-Marge verifizieren
        self._derive_fcf(company, years)      # fcf = OCF − CapEx (nach OCF/capex)
        self._derive_eps(company, years)      # eps = NI / Aktien (GAAP-konsistent)
        self._derive_net_debt(company, years)

    def _ttm_gaap_net_margin(self, company_id, year):
        """GAAP-Nettomarge als Anker fuer die NI-Plausibilitaet. Bevorzugt die
        VOLLJAHRES-Marge des Vorjahres (NI_FY/revenue_FY(year-1)) — die ist
        saisonneutral. Wichtig fuer Firmen mit stark schwankender Quartals-
        Profitabilitaet (Intuit: die Steuersaison-Q3 verzerrt eine Teiljahres-
        TTM-Marge nach oben und blaeht ni_growth auf). Erst danach TTM-Quartale."""
        def fy_val(key, y):
            r = (self._existing(company_id, key, y, period_type="FY", is_forecast=False)
                 or self._existing(company_id, key, y, period_type="FY", is_forecast=True))
            return r.numeric_value if (r is not None and r.numeric_value is not None) else None
        for y in (year - 1, year - 2):
            ni, rv = fy_val("net_income", y), fy_val("revenue", y)
            # auch Schaetz-FY zulassen (year-1 ist meist Ist Q1-Q3 + kleines Q4);
            # nur ein positiver Umsatz noetig.
            if ni is not None and rv is not None and rv > 0:
                return ni / rv
        quarters = []
        for y in (year, year - 1, year - 2):
            for q in ("Q4", "Q3", "Q2", "Q1"):
                ni = self._existing(company_id, "net_income", y, period_type=q, is_forecast=False)
                rv = self._existing(company_id, "revenue", y, period_type=q, is_forecast=False)
                if (ni is not None and ni.numeric_value is not None
                        and rv is not None and rv.numeric_value):
                    quarters.append((ni.numeric_value, rv.numeric_value))
        if len(quarters) >= 2:
            # Bis zu 4 juengste berichtete Quartale (TTM); >=2 reichen als
            # GAAP-Marge-Anker (z.B. Intuit: nur Q1-Q3 des Vorjahres verfuegbar,
            # da das abgeschlossene FY noch keinen 10-K hat).
            ni_sum = sum(n for n, _ in quarters[:4])
            rv_sum = sum(r for _, r in quarters[:4])
            return (ni_sum / rv_sum) if rv_sum else None
        # Fallback: letztes berichtetes FY (Provider-Anker).
        for y in (year - 1, year - 2):
            ni = self._existing(company_id, "net_income", y, period_type="FY", is_forecast=False)
            rv = self._existing(company_id, "revenue", y, period_type="FY", is_forecast=False)
            if (ni is not None and ni.numeric_value is not None
                    and rv is not None and rv.numeric_value and _rank(ni.primary_method) >= 2):
                return ni.numeric_value / rv.numeric_value
        return None

    def _repair_net_income_gaap(self, company, years):
        """Der KI-recherchierte net_income ist bei SBC-schweren Firmen
        unzuverlaessig (Intuit: mal 22B Einheiten-Fehler, mal null; DT: GAAP/
        Non-GAAP-Hybrid). GAAP-Anker: letzte berichtete GAAP-Marge × Umsatz.
        - NI fehlt ganz -> mit dem Anker fuellen (Overview braucht NI).
        - NI grob unplausibel (NI>Umsatz, Vorzeichen, >2x/<0.5x) -> reparieren.
        - NI plausibel -> behalten ('KI findet es, wir verifizieren nur').
        Nur Schaetzjahre; berichtete Actuals bleiben unangetastet."""
        for year in years:
            if not self._needs_estimate_completion(company, year):
                continue
            rev = self._existing(company.id, "revenue", year, period_type="FY", is_forecast=False) \
                or self._existing(company.id, "revenue", year, period_type="FY", is_forecast=True)
            if rev is None or not rev.numeric_value or rev.numeric_value <= 0:
                continue
            margin = self._ttm_gaap_net_margin(company.id, year)
            if margin is None:
                continue
            anchor = margin * rev.numeric_value
            ni = self._existing(company.id, "net_income", year, period_type="FY", is_forecast=True)
            cur = ni.numeric_value if (ni is not None) else None
            has_q = bool(self._reported_quarters(company.id, "net_income", year))
            if has_q and cur is not None:
                # Jahr MIT berichteten Quartalen: NI ist an die Ist-Quartale
                # verankert (Σ Actuals + Q4). Nur grobe Ausreisser reparieren.
                ratio = (cur / anchor) if anchor != 0 else None
                plausible = (
                    abs(cur) <= rev.numeric_value
                    and ((anchor > 0) == (cur > 0))
                    and (ratio is not None and Decimal("0.5") <= ratio <= Decimal("2"))
                )
                if plausible:
                    continue
                label, method = "GAAP-Marge × Umsatz (KI-Wert verworfen)", ni.primary_method
            else:
                # Jahr OHNE Ist-Quartale: der LLM-NI-Konsens ist run-to-run zu
                # instabil (Intuit: 15530/5511/null) fuers Headline-Metric.
                # Deterministischer GAAP-Anker (Vorjahres-Marge × Konsens-Umsatz)
                # -> ni_growth ~ Umsatzwachstum, reproduzierbar & GAAP-konsistent.
                label = "GAAP-Marge × Umsatz (Vorjahres-Marge)"
                method = "estimate_unanchored"
            logger.info("NI-Anker %s FY%s: KI=%s -> %s (Marge %.3f)",
                        company.ticker, year, cur, anchor, float(margin))
            self._upsert(company.id, "net_income", year, period_type="FY", value=anchor,
                         source_name=label, source_link=None,
                         currency=rev.currency, primary_method=method, is_forecast=True)

    def _running_fy_from_quarters(self, company, year, currency):
        """Laufendes FY-Flow = Q1+Q2+Q3 (Actuals) + Q4 (Schaetzung). Verankert an
        die berichtete Realitaet -> FY nie unter YTD, kein negatives Q4. Keys mit
        unvollstaendigen Quartalen (z.B. capex/OCF nicht im Earnings-Release) ->
        volle FY-Schaetzung als Fallback. fcf wird separat abgeleitet."""
        incomplete = []
        for key in _FLOW_KEYS:
            fy_actual = self._existing(company.id, key, year, is_forecast=False)
            if fy_actual is not None and fy_actual.numeric_value is not None:
                continue
            qsum = Decimal("0")
            cur = None
            complete = True
            for q in ("Q1", "Q2", "Q3", "Q4"):
                r = (self._existing(company.id, key, year, period_type=q, is_forecast=False)
                     or self._existing(company.id, key, year, period_type=q, is_forecast=True))
                if r is None or r.numeric_value is None:
                    complete = False
                    break
                qsum += r.numeric_value
                cur = cur or r.currency
            if complete:
                self._upsert(company.id, key, year, period_type="FY",
                             value=normalize_sign(key, qsum, context=f"fy-from-q {company.ticker} FY{year}"),
                             source_name="FY = Q1+Q2+Q3+Q4 (Q4 geschätzt)", source_link=None,
                             currency=cur, primary_method="perplexity_consensus", is_forecast=True)
            else:
                incomplete.append(key)
        incomplete = [k for k in incomplete if k != "fcf"]  # fcf = OCF−CapEx (abgeleitet)
        if incomplete and self.perplexity is not None:
            # Grounding: die bereits berichteten Quartale mitgeben, damit die
            # Guidance/Konsens-Schaetzung realistisch verankert ist (verhindert
            # LLM-Ausreisser wie Q4-NI 3600 statt ~6000).
            reported_context = self._reported_actuals_context(company.id, year, incomplete)
            try:
                vals = self.perplexity.fetch_consensus(
                    company_name=company.name, ticker=company.ticker,
                    forward_year=year, keys=incomplete, currency=currency,
                    reported_context=reported_context)
            except Exception as e:
                logger.warning("perplexity FY-Fallback %s FY%s: %s",
                               getattr(company, "ticker", "?"), year, e)
                vals = {}
            for key, pv in vals.items():
                fy_est = self._unit_fix(company.id, key, Decimal(str(pv.value)))
                # Floor: ein FY-Flow darf nie unter der Summe der BERICHTETEN
                # Ist-Quartale liegen (Perplexity unterschaetzt gerade-gestartete
                # FYs teils grob, z.B. buyback FY=0 trotz Q1-Actual 275). Nur fuer
                # positive Flows (NI kann negativ sein).
                reported = self._reported_quarters(company.id, key, year)
                actual_sum = sum(reported.values(), Decimal("0"))
                if actual_sum > 0 and fy_est < actual_sum:
                    fy_est = actual_sum
                present = {}
                missingq = []
                for q in ("Q1", "Q2", "Q3", "Q4"):
                    r = (self._existing(company.id, key, year, period_type=q, is_forecast=False)
                         or self._existing(company.id, key, year, period_type=q, is_forecast=True))
                    if r is not None and r.numeric_value is not None:
                        present[q] = r.numeric_value
                    else:
                        missingq.append(q)
                # Kein einziges berichtetes Quartal -> unbestaetigte Schaetzung
                # (in der UI eigens markiert). Sonst normale Konsens-Schaetzung.
                unanchored = not reported
                self._upsert(company.id, key, year, period_type="FY", value=fy_est,
                             source_name=("Schätzung – noch kein Quartal berichtet"
                                          if unanchored else "Schätzung (Konsens)"),
                             source_link=pv.source_url, currency=currency,
                             primary_method=("estimate_unanchored" if unanchored
                                             else "perplexity_consensus"), is_forecast=True)
                # Genau EIN fehlendes Quartal (typ. Q3 nicht gebridged) -> als
                # Residuum fuellen, damit Σ Quartale = FY (Konsistenz).
                if len(missingq) == 1:
                    resid = fy_est - sum(present.values(), Decimal("0"))
                    self._upsert(company.id, key, year, period_type=missingq[0], value=resid,
                                 source_name="Geschätzt (Q = FY − andere Quartale)", source_link=None,
                                 currency=currency, primary_method="perplexity_consensus", is_forecast=True)

    def _derive_fcf(self, company, years):
        """fcf = OCF − |CapEx| je Slot (Konvention: FCF nie eigenstaendig
        schaetzen). Ueberschreibt nur nicht-manuelle/nicht-provider fcf."""
        for year in years:
            ocf_rows = (self.db.query(CompanyValue)
                        .filter(CompanyValue.company_id == company.id,
                                CompanyValue.value_key == "operating_cash_flow",
                                CompanyValue.period_year == year,
                                CompanyValue.numeric_value.isnot(None))
                        .all())
            for orow in ocf_rows:
                pt, fc = orow.period_type, orow.is_forecast
                fexist = self._existing(company.id, "fcf", year, period_type=pt, is_forecast=fc)
                if fexist is not None and (fexist.manually_overridden
                                           or fexist.primary_method == "provider"):
                    continue
                cap = self._existing(company.id, "capex", year, period_type=pt, is_forecast=fc)
                if cap is None or cap.numeric_value is None:
                    continue
                self._upsert(company.id, "fcf", year, period_type=pt,
                             value=orow.numeric_value - abs(cap.numeric_value),
                             source_name="Abgeleitet (OCF − CapEx)", source_link=None,
                             currency=orow.currency, primary_method="derived", is_forecast=fc)

    def _derive_eps(self, company, years):
        """eps_diluted = net_income / aktuelle Aktienzahl. Fuellt leere eps
        (EDGAR hat bei manchen Firmen das Standard-EPS-Concept nicht, z.B. Visa)
        UND ersetzt Konsens-/Schaetz-eps (oft Non-GAAP, z.B. Intuit ~$19 statt
        GAAP ~$12) durch den GAAP-konsistenten Wert aus GAAP-NI/Aktien. Ein
        berichteter (provider) oder manueller eps bleibt unangetastet."""
        snap = self._existing(company.id, "shares_outstanding", None,
                              period_type="SNAPSHOT", is_forecast=False)
        shares = snap.numeric_value if (snap and snap.numeric_value) else None
        if not shares or shares == 0:
            return
        for year in years:
            ni_rows = (self.db.query(CompanyValue)
                       .filter(CompanyValue.company_id == company.id,
                               CompanyValue.value_key == "net_income",
                               CompanyValue.period_year == year,
                               CompanyValue.numeric_value.isnot(None))
                       .all())
            for nirow in ni_rows:
                pt, fc = nirow.period_type, nirow.is_forecast
                existing = self._existing(company.id, "eps_diluted", year, period_type=pt, is_forecast=fc)
                if existing is not None and existing.numeric_value is not None and (
                        existing.manually_overridden or existing.primary_method == "provider"):
                    continue  # GAAP-Actual/manuell -> behalten
                self._upsert(company.id, "eps_diluted", year, period_type=pt,
                             value=nirow.numeric_value / shares,
                             source_name="Abgeleitet (NI / Aktien)", source_link=None,
                             currency=None, primary_method="derived", is_forecast=fc)

    def _derive_net_debt(self, company, years):
        """net_debt = (st_debt + lt_debt) − (cash + st_investments) je Slot, in dem
        Cash vorliegt. Fehlende Schuld/Investment-Komponenten = 0. Ersetzt die
        (unzuverlaessige) Perplexity-net_debt-Schaetzung durch eine Ableitung."""
        for year in years:
            cash_rows = (self.db.query(CompanyValue)
                         .filter(CompanyValue.company_id == company.id,
                                 CompanyValue.value_key == "cash_and_equivalents",
                                 CompanyValue.period_year == year,
                                 CompanyValue.numeric_value.isnot(None))
                         .all())
            for cr in cash_rows:
                pt, fc = cr.period_type, cr.is_forecast
                existing = self._existing(company.id, "net_debt", year, period_type=pt, is_forecast=fc)
                if existing is not None and (existing.manually_overridden
                                             or existing.primary_method == "provider"):
                    continue

                def comp(k, _pt=pt, _fc=fc):
                    r = self._existing(company.id, k, year, period_type=_pt, is_forecast=_fc)
                    return r.numeric_value if (r is not None and r.numeric_value is not None) else Decimal("0")

                net_debt = (comp("st_debt") + comp("lt_debt")) - (cr.numeric_value + comp("st_investments"))
                self._upsert(company.id, "net_debt", year, period_type=pt, value=net_debt,
                             source_name="Abgeleitet (Schulden − Cash)", source_link=None,
                             currency=cr.currency, primary_method="derived", is_forecast=fc)

    def _derive_calculations(self, company, years):
        # Lazy import: run_and_persist_calculations_for_years lebt in routes.py.
        # Lazy vermeidet den Zyklus routes -> orchestrator -> routes.
        from app.values.routes import run_and_persist_calculations_for_years
        run_and_persist_calculations_for_years(self.db, company, years)
