from decimal import Decimal
from app.core.rules import eligible, median_odds, parse_decimal_odd, triggered


def test_median():
    assert median_odds(dict(enumerate(["1.25","1.25","1.29","1.25","1.28","1.26","1.27"]))) == Decimal("1.26")
    assert median_odds({"a":"1.28"}) == Decimal("1.28")
    assert median_odds({"a":"1.25","b":None,"c":"","d":"abc","e":"1.29"}) == Decimal("1.27")


def test_boundaries():
    assert [eligible(parse_decimal_odd(x),Decimal("1.01"),Decimal("1.35")) for x in ["1.00","1.01","1.20","1.35","1.36"]] == [False,True,True,True,False]
    assert [triggered(parse_decimal_odd(x),Decimal("2")) for x in ["1.99","2.00","2.01"]] == [False,True,True]
