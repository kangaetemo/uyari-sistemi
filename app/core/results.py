"""Match results and the alert hit-rate report.

The report answers the question the alerts exist for: when the system said
"the followed side drifted to 2.00+", how often did that side go on to win?
At decimal odds `o` a flat stake breaks even at a win rate of 1/o, so the
report prints both the observed win rate and that break-even rate.
"""
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

SIDE_BY_WINNER = {"first player": "Home", "home": "Home", "second player": "Away", "away": "Away"}
RULE_NAMES = {"PREMATCH": "Maç önü favorisi", "THIRD_SET": "3. set başlangıcı"}


@dataclass(frozen=True)
class MatchResult:
    winner_side: str | None
    final_result: str | None
    provider_status: str
    outcome_type: str  # FINISHED, RETIRED, WALKOVER, CANCELLED


def parse_result(fixture):
    """Return the settled result of a fixture, or None while it is not final."""
    status = str(fixture.get("event_status") or "").strip()
    lowered = status.lower()
    if "postpon" in lowered or "interrupt" in lowered or "suspend" in lowered:
        return None  # Not final; the match may still be played.
    if "walk" in lowered:
        outcome = "WALKOVER"
    elif "retired" in lowered:
        outcome = "RETIRED"
    elif "cancel" in lowered or "abandon" in lowered:
        outcome = "CANCELLED"
    elif "finish" in lowered or "ended" in lowered:
        outcome = "FINISHED"
    else:
        return None
    final = fixture.get("event_final_result")
    final = str(final).strip() if final not in (None, "", "-") else None
    winner = SIDE_BY_WINNER.get(str(fixture.get("event_winner") or "").strip().lower())
    if winner is None and outcome == "FINISHED" and final:
        sets = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", final)
        if sets and sets[1] != sets[2]:
            winner = "Home" if int(sets[1]) > int(sets[2]) else "Away"
    if outcome == "CANCELLED":
        winner = None
    return MatchResult(winner, final, status, outcome)


def alert_outcome(row):
    """WON / LOST for the followed side, VOID when nothing was settled, PENDING without a result."""
    if row["outcome_type"] is None:
        return "PENDING"
    if row["outcome_type"] in {"CANCELLED", "WALKOVER"} or row["winner_side"] is None:
        return "VOID"
    return "WON" if row["winner_side"] == row["favorite_side"] else "LOST"


def build_report(rows):
    stats = defaultdict(lambda: {"total": 0, "WON": 0, "LOST": 0, "PENDING": 0, "VOID": 0,
                                 "retired": 0, "profit": Decimal(0), "odds_sum": Decimal(0)})
    for row in rows:
        for rule in (row["rule_type"], "ALL"):
            item = stats[rule]
            outcome = alert_outcome(row)
            odd = Decimal(row["trigger_live_odds"])
            item["total"] += 1
            item[outcome] += 1
            item["odds_sum"] += odd
            if outcome in {"WON", "LOST"} and row["outcome_type"] == "RETIRED":
                item["retired"] += 1
            if outcome == "WON":
                item["profit"] += odd - 1
            elif outcome == "LOST":
                item["profit"] -= 1
    return stats


def format_report(rows, days):
    stats = build_report(rows)
    lines = [f"Son {days} gün alarm sonuçları"]
    if not rows:
        lines.append("Bu dönemde alarm yok.")
        return "\n".join(lines)
    for rule in [r for r in ("PREMATCH", "THIRD_SET") if r in stats] + ["ALL"]:
        item = stats[rule]
        settled = item["WON"] + item["LOST"]
        avg_odds = item["odds_sum"] / item["total"]
        lines.append("")
        lines.append(RULE_NAMES.get(rule, "Toplam"))
        lines.append(f"  Alarm: {item['total']} | Kazandı: {item['WON']} | Kaybetti: {item['LOST']} | "
                     f"Bekliyor: {item['PENDING']} | Geçersiz: {item['VOID']}")
        if settled:
            lines.append(f"  Kazanma oranı: %{item['WON'] / settled * 100:.1f} ({settled} sonuçlanmış alarm)")
            lines.append(f"  Ortalama alarm oranı: {avg_odds:.2f} | Başabaş kazanma oranı: %{100 / avg_odds:.1f}")
            lines.append(f"  Teorik getiri (her alarma 1 birim, alarm oranından): {item['profit']:+.2f} birim, "
                         f"%{item['profit'] / settled * 100:+.1f}")
            if item["retired"]:
                lines.append(f"  Not: {item['retired']} sonuç maçtan çekilmeyle bitti; bahis sitelerinin kuralları farklı olabilir.")
            if settled < 100:
                lines.append("  Uyarı: 100'den az sonuç var; oran istatistiksel olarak henüz güvenilir değil.")
    lines.append("")
    lines.append("Teorik getiri, alarm anındaki oranın alınabildiğini varsayar; gerçek bahis kaydı değildir.")
    return "\n".join(lines)
