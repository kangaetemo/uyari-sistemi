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
RULE_NAMES = {"PREMATCH": "Maç önü favorisi", "THIRD_SET": "3. set başlangıcı", "SERVE_FOR_SET": "Set için servis"}
RULES = tuple(RULE_NAMES)
# Rules judged by the match result; their totals are the "maç sonu" statistics.
MATCH_RULES = ("PREMATCH", "THIRD_SET")
# Rules judged by the game that follows the alert (did the favourite break back?).
SET_RULES = ("SERVE_FOR_SET",)
BREAK_RESULTS = {"BROKE": "WON", "HELD": "LOST"}
# Bookmaker margin assumed when an alert has no stored opponent price (alerts made
# before alerts.opponent_odds existed, or the opponent's price was suspended).
OPPONENT_MARGIN = Decimal("0.05")


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


def break_outcome(row):
    """WON when the favourite broke the player serving for the set, LOST when that player held.

    VOID when the match ended (retirement, walkover...) before the game was settled.
    """
    if row["game_outcome"] in BREAK_RESULTS:
        return BREAK_RESULTS[row["game_outcome"]]
    return "VOID" if row["outcome_type"] is not None else "PENDING"


def set_outcome(row):
    """WON / LOST for the favourite in the alerted set, VOID when the match ended without it."""
    if row["set_outcome"] in {"WON", "LOST"}:
        return row["set_outcome"]
    return "VOID" if row["outcome_type"] is not None else "PENDING"


def rule_outcome(row):
    """The outcome each rule is judged by: the break for set rules, the match result for the others."""
    return break_outcome(row) if row["rule_type"] in SET_RULES else alert_outcome(row)


def estimate_opponent_odds(favorite_odds, margin=OPPONENT_MARGIN):
    """The opponent's price implied by the favourite's, for a two-way market with `margin` overround."""
    rest = 1 + margin - 1 / Decimal(favorite_odds)
    return (1 / rest).quantize(Decimal("0.01")) if rest > 0 else None


def opponent_odds(row):
    """(price, estimated) of backing the other player at the alert: the stored price, else an estimate."""
    stored = row["opponent_odds"] if "opponent_odds" in row.keys() else None
    if stored:
        return Decimal(stored), False
    estimate = estimate_opponent_odds(row["trigger_live_odds"])
    return (estimate, True) if estimate is not None else (None, False)


def build_report(rows):
    """Match-result counters per rule; "ALL" adds up only the match rules (MATCH_RULES).

    The opp_* counters replay the opposite bet: 1 unit on the other player at the
    alert, at the opponent's price, judged by the same match result.
    """
    stats = defaultdict(lambda: {"total": 0, "WON": 0, "LOST": 0, "PENDING": 0, "VOID": 0,
                                 "retired": 0, "profit": Decimal(0), "odds_sum": Decimal(0),
                                 "opp_settled": 0, "opp_profit": Decimal(0), "opp_odds_sum": Decimal(0),
                                 "opp_estimated": 0})
    for row in rows:
        rules = (row["rule_type"], "ALL") if row["rule_type"] in MATCH_RULES else (row["rule_type"],)
        for rule in rules:
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
            opp, estimated = opponent_odds(row)
            if outcome in {"WON", "LOST"} and opp is not None:
                item["opp_settled"] += 1
                item["opp_odds_sum"] += opp
                item["opp_estimated"] += estimated
                item["opp_profit"] += opp - 1 if outcome == "LOST" else -1
    return stats


def opponent_line(item):
    """One report line for the opposite bet, or None when nothing is settled."""
    if not item["opp_settled"]:
        return None
    avg = item["opp_odds_sum"] / item["opp_settled"]
    line = (f"  Rakibe oynasaydık (alarm anındaki rakip oranından 1 birim): {item['opp_profit']:+.2f} birim, "
            f"%{item['opp_profit'] / item['opp_settled'] * 100:+.1f} | ortalama rakip oranı {avg:.2f}, "
            f"başabaş %{100 / avg:.1f}")
    if item["opp_estimated"]:
        line += f" | {item['opp_estimated']} oran tahmini"
    return line


def serve_report(rows):
    """Serving-for-set alerts judged on their own terms: the break, the set, and then the match.

    None when there is no such alert. `match` holds the usual match-result
    counters of the same alerts, kept apart from the match rules' totals.
    """
    items = [row for row in rows if row["rule_type"] in SET_RULES]
    if not items:
        return None
    out = {"total": len(items)}
    for name, judge in (("break", break_outcome), ("set", set_outcome)):
        counts = {key: 0 for key in ("WON", "LOST", "PENDING", "VOID")}
        for row in items:
            counts[judge(row)] += 1
        settled = counts["WON"] + counts["LOST"]
        out[name] = {**counts, "settled": settled, "rate": counts["WON"] / settled * 100 if settled else None}
    # After a break the set is still open; this is how often the favourite then took it.
    after = [row["set_outcome"] for row in items if row["game_outcome"] == "BROKE" and row["set_outcome"] in {"WON", "LOST"}]
    out["set_after_break"] = {"WON": after.count("WON"), "settled": len(after),
                              "rate": after.count("WON") / len(after) * 100 if after else None}
    out["match"] = build_report(items)[SET_RULES[0]]
    return out


def format_report(rows, days):
    stats = build_report(rows)
    lines = [f"Son {days} gün alarm sonuçları"]
    if not rows:
        lines.append("Bu dönemde alarm yok.")
        return "\n".join(lines)
    match_rules = [r for r in MATCH_RULES if r in stats]
    if match_rules:
        lines.append("")
        lines.append("== Maç sonu alarmları (takip edilen taraf maçı kazandı mı) ==")
    for rule in match_rules + (["ALL"] if match_rules else []):
        item = stats[rule]
        settled = item["WON"] + item["LOST"]
        avg_odds = item["odds_sum"] / item["total"]
        lines.append("")
        lines.append(RULE_NAMES.get(rule, "Maç sonu toplamı"))
        lines.append(f"  Alarm: {item['total']} | Kazandı: {item['WON']} | Kaybetti: {item['LOST']} | "
                     f"Bekliyor: {item['PENDING']} | Geçersiz: {item['VOID']}")
        if settled:
            lines.append(f"  Kazanma oranı: %{item['WON'] / settled * 100:.1f} ({settled} sonuçlanmış alarm)")
            lines.append(f"  Ortalama alarm oranı: {avg_odds:.2f} | Başabaş kazanma oranı: %{100 / avg_odds:.1f}")
            lines.append(f"  Teorik getiri (her alarma 1 birim, alarm oranından): {item['profit']:+.2f} birim, "
                         f"%{item['profit'] / settled * 100:+.1f}")
            opponent = opponent_line(item)
            if opponent:
                lines.append(opponent)
            if item["retired"]:
                lines.append(f"  Not: {item['retired']} sonuç maçtan çekilmeyle bitti; bahis sitelerinin kuralları farklı olabilir.")
            if settled < 100:
                lines.append("  Uyarı: 100'den az sonuç var; oran istatistiksel olarak henüz güvenilir değil.")
    serve = serve_report(rows)
    if serve:
        lines.append("")
        lines.append("== Set kırma alarmları (set için servis) ==")
        lines.append(f"  Alarm: {serve['total']}")
        for key, label in (("break", "Break yaptı"), ("set", "Seti aldı")):
            item = serve[key]
            rate = f" (%{item['rate']:.1f})" if item["rate"] is not None else ""
            lines.append(f"  {label}: {item['WON']} / {item['settled']}{rate} | Bekliyor: {item['PENDING']} | Geçersiz: {item['VOID']}")
        after = serve["set_after_break"]
        if after["settled"]:
            lines.append(f"  Break sonrası seti aldı: {after['WON']} / {after['settled']} (%{after['rate']:.1f})")
        match = serve["match"]
        settled = match["WON"] + match["LOST"]
        lines.append(f"  Ayrıca maç sonucu: Kazandı: {match['WON']} | Kaybetti: {match['LOST']} | Bekliyor: {match['PENDING']}"
                     + (f" | Kazanma oranı: %{match['WON'] / settled * 100:.1f}" if settled else ""))
        opponent = opponent_line(match)
        if opponent:
            lines.append(opponent)
        lines.append("  Not: Bu alarmlar maç sonu toplamına katılmaz; alarm oranı maç oranı olduğu için teorik getiri yalnızca maç sonucuna göre hesaplanır.")
        if serve["break"]["settled"] and serve["break"]["settled"] < 100:
            lines.append("  Uyarı: 100'den az sonuç var; oran istatistiksel olarak henüz güvenilir değil.")
    lines.append("")
    lines.append("Teorik getiri, alarm anındaki oranın alınabildiğini varsayar; gerçek bahis kaydı değildir.")
    if any(opponent_odds(row)[1] for row in rows):
        lines.append(f"Rakip oranı kaydedilmemiş alarmlarda (bu özellikten önceki alarmlar) rakip oranı, favorinin alarm "
                     f"oranından %{OPPONENT_MARGIN * 100:.0f} bahis marjı varsayılarak tahmin edildi.")
    return "\n".join(lines)
