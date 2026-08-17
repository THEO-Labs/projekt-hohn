"""Baut das response_format json_schema fuer Perplexity-Abfragen aus den
Fundamental-Keys (Katalog-API-Keys minus Stammdaten). Jede Kennzahl ist ein
nullable number; Definitionen kommen aus metric_definitions. Keine URL-Felder
(Quellen kommen aus citations).
"""

from app.values.always_current import ALWAYS_CURRENT_KEYS
from app.values.metric_definitions import ADJUSTED_KEYS, METRIC_DEFINITIONS


def fundamental_keys() -> list[str]:
    return [k for k in METRIC_DEFINITIONS if k not in ALWAYS_CURRENT_KEYS]


def _num_prop(desc: str) -> dict:
    return {"type": ["number", "null"], "description": desc}


def _properties() -> dict:
    props: dict[str, dict] = {}
    for k in fundamental_keys():
        props[k] = _num_prop(METRIC_DEFINITIONS[k])
        if k in ADJUSTED_KEYS:
            props[f"{k}_adjusted"] = _num_prop(
                f"Company-reported adjusted / non-GAAP variant of: {METRIC_DEFINITIONS[k]} "
                "Only if the company explicitly reports an adjusted figure; else null."
            )
    return props


def build_period_schema(name: str = "fundamentals") -> dict:
    props = _properties()
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "schema": {
                "type": "object",
                "properties": props,
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def build_consensus_schema(keys: list[str]) -> dict:
    props = {k: _num_prop(METRIC_DEFINITIONS[k]) for k in keys if k in METRIC_DEFINITIONS}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "consensus",
            "schema": {"type": "object", "properties": props,
                       "required": [], "additionalProperties": False},
        },
    }
