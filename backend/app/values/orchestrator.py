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
        self._emit("perplexity", "Perplexity-Schätzung")
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
        run = years[-1]
        for year in years:
            if year == run and not _fy_is_closed(company, year):
                self._estimate_running_fy(company, year, currency)  # Q4-Schaetzung + Balance-Carry
            else:
                self._fill_reported_gaps(company, year, currency)   # abgeschlossenes Jahr: nur Luecken

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
            self._upsert(company.id, key, year, value=val, source_name="Perplexity",
                         source_link=pv.source_url, currency=currency,
                         primary_method="perplexity", is_forecast=False, adjusted=adj)

    def _estimate_running_fy(self, company, year, currency):
        """Laufendes FY. Zwei Faelle:
        - Q1-Q3 berichtet (spaet im FY, z.B. Sep-FY im August): nur Q4 schaetzen,
          FY = Q1+Q2+Q3+Q4 (verankert an die Realitaet).
        - Q1-Q3 (noch) NICHT berichtet (frueh im FY, z.B. Juni-FY im August):
          KEIN einsames Q4 — das ganze FY direkt schaetzen (Konsens)."""
        flow_keys = sorted(_FLOW_KEYS)
        self._clear_forecast_slots(company.id, year, flow_keys + list(_BALANCE_KEYS) + ["net_debt"],
                                   ("Q4", "FY"))
        # A) Bridge: berichtete Quartale, die EDGAR noch nicht im XBRL hat
        #    (Filing-Lag, nur Press-Release/8-K), aus dem Earnings-Release holen.
        self._bridge_missing_quarters(company, year, currency)
        reported_q = sum(
            1 for q in ("Q1", "Q2", "Q3")
            if self._has_reported(company.id, "revenue", year, q)
        )
        if reported_q >= 3:
            self._estimate_q4(company, year, flow_keys, currency)
        else:
            self._estimate_full_fy(company, year, flow_keys, currency)
        self._carry_forward_balances(company, year, currency)

    def _has_reported(self, company_id, key, year, period_type):
        r = self._existing(company_id, key, year, period_type=period_type, is_forecast=False)
        return r is not None and r.numeric_value is not None

    def _bridge_missing_quarters(self, company, year, currency):
        """Berichtete Quartale, deren 10-Q-XBRL noch nicht bei EDGAR ist (Lag),
        via Perplexity aus dem Earnings-Release fuellen. primary_method=
        'perplexity' (is_forecast=False) -> der EDGAR-Anker (rank provider)
        ueberschreibt sie, sobald das XBRL nachkommt."""
        from datetime import date, timedelta

        from app.values.detail_page import quarter_end_date
        from app.values.schema_builder import fundamental_keys
        today = date.today()
        lag = timedelta(days=35)  # Earnings ~3-5 Wochen nach Quartalsende
        fym = getattr(company, "fiscal_year_end_month", None)
        fyd = getattr(company, "fiscal_year_end_day", None)
        all_keys = fundamental_keys()
        for q in ("Q1", "Q2", "Q3"):
            qend = quarter_end_date(year, q, fym, fyd)
            if qend is None or qend + lag > today:
                continue  # Quartal (noch) nicht berichtet
            if self._has_reported(company.id, "revenue", year, q):
                continue  # EDGAR hat es bereits
            missing = [k for k in all_keys if not self._has_reported(company.id, k, year, q)]
            if not missing:
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
                             source_name="Perplexity (Earnings-Release)", source_link=pv.source_url,
                             currency=currency, primary_method="perplexity", is_forecast=False)
        self.db.flush()

    def _estimate_q4(self, company, year, flow_keys, currency):
        try:
            q4 = self.perplexity.fetch_quarter_estimate(
                company_name=company.name, ticker=company.ticker,
                fiscal_year=year, quarter="Q4", keys=flow_keys, currency=currency)
        except Exception as e:
            logger.warning("perplexity Q4-Schaetzung fehlgeschlagen %s FY%s: %s",
                           getattr(company, "ticker", "?"), year, e)
            return
        for key, pv in q4.items():
            val = self._unit_fix(company.id, key, Decimal(str(pv.value)))
            self._upsert(company.id, key, year, period_type="Q4", value=val,
                         source_name="Perplexity (Q4-Schätzung)", source_link=pv.source_url,
                         currency=currency, primary_method="perplexity_consensus",
                         is_forecast=True)

    def _estimate_full_fy(self, company, year, flow_keys, currency):
        try:
            vals = self.perplexity.fetch_consensus(
                company_name=company.name, ticker=company.ticker,
                forward_year=year, keys=flow_keys, currency=currency)
        except Exception as e:
            logger.warning("perplexity FY-Schaetzung fehlgeschlagen %s FY%s: %s",
                           getattr(company, "ticker", "?"), year, e)
            return
        for key, pv in vals.items():
            val = self._unit_fix(company.id, key, Decimal(str(pv.value)))
            self._upsert(company.id, key, year, period_type="FY", value=val,
                         source_name="Perplexity (FY-Schätzung)", source_link=pv.source_url,
                         currency=currency, primary_method="perplexity_consensus",
                         is_forecast=True)

    def _carry_forward_balances(self, company, year, currency):
        """Bilanz-Keys fuers laufende FY: Jahresend-Stichtag ~ letztes berichtetes
        Quartal (Carry-Forward). Deterministisch, keine Schaetzung."""
        for key in _BALANCE_KEYS:
            fy_actual = self._existing(company.id, key, year, is_forecast=False)
            if fy_actual is not None and fy_actual.numeric_value is not None:
                continue
            latest = None
            for q in ("Q3", "Q2", "Q1"):
                r = self._existing(company.id, key, year, period_type=q, is_forecast=False)
                if r is not None and r.numeric_value is not None:
                    latest = r
                    break
            if latest is None:
                # Fallback: letzter Jahresschluss (FY-1) als Proxy (Bilanz aendert
                # sich langsam) — deckt Keys ab, die EDGAR nur jaehrlich liefert (st_debt).
                prev = self._existing(company.id, key, year - 1, period_type="FY", is_forecast=False)
                if prev is not None and prev.numeric_value is not None:
                    latest = prev
            if latest is None:
                continue
            self._upsert(company.id, key, year, period_type="FY", value=latest.numeric_value,
                         source_name="Carry-Forward (letzter Stichtag)", source_link=None,
                         currency=latest.currency or currency,
                         primary_method="perplexity_consensus", is_forecast=True)

    def _finalize_estimates(self, company, years):
        """Nach EDGAR + Perplexity: abgeschlossene Jahre Q4=FY−Q1-Q3; laufendes
        FY = Q1+Q2+Q3+Q4(geschaetzt); net_debt deterministisch aus Komponenten."""
        from app.values.provider_anchor import _fy_is_closed
        from app.values.quarter_residual import derive_q4_from_fy_residual
        run = years[-1]
        currency = getattr(company, "currency", None) or "USD"
        closed = [y for y in years if _fy_is_closed(company, y)]
        if closed:
            derive_q4_from_fy_residual(self.db, company, closed)
        for y in years:
            if y == run and not _fy_is_closed(company, y):
                self._running_fy_from_quarters(company, y, currency)
        self.db.flush()  # OCF/capex-Fallback sichtbar machen fuer _derive_fcf
        self._derive_fcf(company, years)      # fcf = OCF − CapEx (nach OCF/capex)
        self._derive_eps(company, years)      # eps = NI / Aktien, wo EDGAR/Perplexity leer
        self._derive_net_debt(company, years)

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
            try:
                vals = self.perplexity.fetch_consensus(
                    company_name=company.name, ticker=company.ticker,
                    forward_year=year, keys=incomplete, currency=currency)
            except Exception as e:
                logger.warning("perplexity FY-Fallback %s FY%s: %s",
                               getattr(company, "ticker", "?"), year, e)
                vals = {}
            for key, pv in vals.items():
                val = self._unit_fix(company.id, key, Decimal(str(pv.value)))
                self._upsert(company.id, key, year, period_type="FY", value=val,
                             source_name="Perplexity (FY-Schätzung)", source_link=pv.source_url,
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
        """eps_diluted = net_income / aktuelle Aktienzahl, NUR wo eps leer ist
        (EDGAR hat bei manchen Firmen das Standard-EPS-Concept nicht, z.B. Visa)."""
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
                if existing is not None and existing.numeric_value is not None:
                    continue  # eps schon da (EDGAR/Perplexity/manual) -> nicht anfassen
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
