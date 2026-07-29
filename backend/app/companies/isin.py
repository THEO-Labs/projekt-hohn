import re

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")


def is_us_isin(isin: str | None) -> bool:
    """US-Filer werden am ISIN-Praefix "US" erkannt."""
    return bool(isin and isin.upper().startswith("US"))


def accounting_standard(isin: str | None) -> str:
    """Rechnungslegungs-Regelwerk der Firma, abgeleitet aus der ISIN:
    US-Filer berichten nach US-GAAP, alle anderen nach IFRS.
    Berechnetes Response-Feld, kein DB-Feld."""
    return "US-GAAP" if is_us_isin(isin) else "IFRS"


def validate_isin(value: str) -> bool:
    if not _ISIN_RE.match(value):
        return False
    digits = _expand_to_digits(value[:-1])
    return _luhn_checksum(digits, int(value[-1])) == 0


def _expand_to_digits(chars: str) -> list[int]:
    result = []
    for ch in chars:
        if ch.isdigit():
            result.append(int(ch))
        else:
            n = ord(ch) - ord("A") + 10
            result.extend(int(d) for d in str(n))
    return result


def _luhn_checksum(digits: list[int], check_digit: int) -> int:
    all_digits = digits + [check_digit]
    total = 0
    for i, d in enumerate(reversed(all_digits)):
        if i % 2 == 1:
            doubled = d * 2
            total += doubled // 10 + doubled % 10
        else:
            total += d
    return total % 10
