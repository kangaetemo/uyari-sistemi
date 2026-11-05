from decimal import Decimal, InvalidOperation
from statistics import median


def parse_decimal_odd(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        odd = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return odd if odd.is_finite() and odd > 1 else None


def median_odds(bookmakers):
    if not isinstance(bookmakers, dict):
        return None
    values = [odd for item in bookmakers.values() if (odd := parse_decimal_odd(item)) is not None]
    return median(values) if values else None


def eligible(odd, minimum, maximum):
    return odd is not None and minimum <= odd <= maximum


def triggered(odd, threshold):
    return odd is not None and odd >= threshold
