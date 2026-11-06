"""Statistics panel: a small FastAPI site that reads the bot's SQLite database."""
import json
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.results import RULE_NAMES, RULES, alert_outcome
from app.web import queries

HERE = Path(__file__).parent
OUTCOME_NAMES = {"WON": "Kazandı", "LOST": "Kaybetti", "PENDING": "Bekliyor", "VOID": "Geçersiz"}
GAME_OUTCOMES = {"BROKE": "break yaptı", "HELD": "rakip servisini aldı"}
SET_OUTCOMES = {"WON": "seti aldı", "LOST": "seti kaybetti"}
STATUS_NAMES = {"PREMATCH_ELIGIBLE": "Maç önü, uygun", "PREMATCH_INELIGIBLE": "Maç önü, uygun değil", "LIVE": "Canlı",
                "ALERTED": "Alarm verildi", "FINISHED": "Bitti", "CANCELLED": "İptal", "EXPIRED": "Süresi doldu",
                "WATCHING": "İzleniyor", "CLOSED": "Kapandı", "ALERTED_CLOSED": "Alarm verildi, kapandı"}
PERIODS = [(1, "24 saat"), (7, "7 gün"), (30, "30 gün"), (0, "Tümü")]
LIVE_PERIODS = [(6, "6 saat"), (12, "12 saat"), (24, "24 saat"), (48, "48 saat")]
FINAL_STATUS_NAMES = (("retired", "Çekilme"), ("walk", "Walkover"), ("cancel", "İptal"), ("abandon", "Yarıda kaldı"),
                      ("finish", "Bitti"), ("ended", "Bitti"))


def match_status(value):
    """Provider status in Turkish: 'Set 2' -> '2. set', 'Finished' -> 'Bitti'."""
    text = str(value or "").strip()
    lowered = text.lower()
    if lowered.startswith("set ") and lowered[4:].isdigit():
        return f"{lowered[4:]}. set"
    for word, name in FINAL_STATUS_NAMES:
        if word in lowered:
            return name
    return text or "-"


def serving(serve, side):
    value = str(serve or "").lower()
    return (side == "Home" and value.startswith("first")) or (side == "Away" and value.startswith("second"))


def create_app(database_path: Path, username: str, password: str, tz):
    app = FastAPI(title="Tenis alarm paneli", docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    # Chart.js 4.4.1 (MIT) is vendored so the panel works without a CDN.
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    security = HTTPBasic(realm="Tenis alarm paneli")

    def require_login(credentials: HTTPBasicCredentials = Depends(security)):
        user_ok = secrets.compare_digest(credentials.username.encode(), username.encode())
        pass_ok = secrets.compare_digest(credentials.password.encode(), password.encode())
        if not (user_ok and pass_ok):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Giriş gerekli", {"WWW-Authenticate": 'Basic realm="Tenis alarm paneli"'})

    def local(value, fmt="%d.%m %H:%M"):
        if not value:
            return "-"
        try:
            return datetime.fromisoformat(value).astimezone(tz).strftime(fmt)
        except (TypeError, ValueError):
            return str(value)

    def ago(value):
        if not value:
            return None
        try:
            return int((datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds())
        except (TypeError, ValueError):
            return None

    def pct(value):
        return "-" if value is None else f"%{value:.1f}"

    def num(value, digits=2):
        return "-" if value is None else f"{Decimal(value):.{digits}f}"

    def signed(value):
        return "-" if value is None else f"{value:+.2f}"

    templates.env.globals.update(local=local, ago=ago, pct=pct, num=num, signed=signed, rule_names=RULE_NAMES,
                                 outcome_names=OUTCOME_NAMES, status_names=STATUS_NAMES, periods=PERIODS,
                                 outcome=alert_outcome, match_status=match_status, serving=serving,
                                 live_periods=LIVE_PERIODS, rules=RULES, game_outcomes=GAME_OUTCOMES,
                                 set_outcomes=SET_OUTCOMES)

    def render(request, name, **context):
        conn = context.pop("_conn", None)
        if conn is not None:
            context["state"] = queries.bot_state(conn)
        return templates.TemplateResponse(request, name, context)

    def open_db():
        conn = queries.connect(database_path)
        if conn is None:
            raise HTTPException(503, "Veritabanı henüz oluşmadı; bot en az bir kez çalışmalı.")
        return conn

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def overview(request: Request, days: int = Query(30, ge=0, le=3650)):
        conn = open_db()
        try:
            data = queries.overview(conn, days)
            labels, counts = queries.daily_counts(queries.alerts(conn, 30), tz)
            chart = {"labels": [label[5:] for label in labels],
                     "series": {key: [counts[label][key] for label in labels] for key in OUTCOME_NAMES}}
            return render(request, "overview.html", _conn=conn, days=days, data=data, chart=json.dumps(chart), page="overview")
        finally:
            conn.close()

    @app.get("/alerts", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def alert_list(request: Request, days: int = Query(30, ge=0, le=3650), rule: str = "", result: str = ""):
        conn = open_db()
        try:
            rows = queries.alerts(conn, days)
            if rule in RULES:
                rows = [r for r in rows if r["rule_type"] == rule]
            if result in OUTCOME_NAMES:
                rows = [r for r in rows if alert_outcome(r) == result]
            return render(request, "alerts.html", _conn=conn, days=days, rows=rows, rule=rule, result=result, page="alerts")
        finally:
            conn.close()

    def alert_index(rows, alert):
        """Position of the stored price that fired the alert (same write), for the chart marker."""
        if alert is None:
            return None
        for index, row in enumerate(rows):
            if row["observed_at"] == alert["created_at"] and row["live_odds"] == alert["trigger_live_odds"]:
                return index
        return next((i for i, row in enumerate(rows) if row["live_odds"] == alert["trigger_live_odds"]), None)

    @app.get("/match/{event_key}", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def match(request: Request, event_key: str):
        conn = open_db()
        try:
            detail = queries.match_detail(conn, event_key)
            if detail is None:
                raise HTTPException(404, "Maç bulunamadı")
            charts = []
            if detail["live"]:
                alert = next((a for a in detail["alerts"] if a["rule_type"] == "PREMATCH"), None)
                charts.append({"title": "Canlı maç kazanma oranı (maç önü kuralı)",
                               "labels": [local(r["observed_at"], "%H:%M:%S") for r in detail["live"]],
                               "values": [float(r["live_odds"]) for r in detail["live"]],
                               "threshold": float(alert["threshold"]) if alert else None,
                               "reference": float(detail["match"]["latest_prematch_odds"]) if detail["match"]["latest_prematch_odds"] else None,
                               "alert_index": alert_index(detail["live"], alert)})
            for watch in detail["third_watches"]:
                rows = [r for r in detail["third"] if r["favorite_side"] == watch["favorite_side"]]
                alert = next((a for a in detail["alerts"] if a["rule_type"] == "THIRD_SET" and a["favorite_side"] == watch["favorite_side"]), None)
                charts.append({"title": f"3. set ({watch['favorite_side']}) maç kazanma oranı",
                               "labels": [local(r["observed_at"], "%H:%M:%S") for r in rows],
                               "values": [float(r["live_odds"]) for r in rows],
                               "threshold": float(alert["threshold"]) if alert else 2.0,
                               "reference": float(watch["opening_odds"]),
                               "alert_index": alert_index(rows, alert)})
            return render(request, "match.html", _conn=conn, detail=detail, charts=json.dumps(charts), page="match")
        finally:
            conn.close()

    @app.get("/active", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def active(request: Request):
        conn = open_db()
        try:
            matches, third = queries.active(conn)
            return render(request, "active.html", _conn=conn, matches=matches, third=third, page="active")
        finally:
            conn.close()

    @app.get("/live", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def live(request: Request, hours: int = Query(24, ge=1, le=168), partial: bool = False):
        conn = open_db()
        try:
            rows, counts = queries.live_alerts(conn, hours)
            name = "_live_cards.html" if partial else "live.html"
            return render(request, name, _conn=conn, rows=rows, counts=counts, hours=hours, page="live",
                          now_iso=datetime.now(timezone.utc).isoformat())
        finally:
            conn.close()

    @app.get("/analysis", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def analysis(request: Request, rule: str = "PREMATCH", days: int = Query(0, ge=0, le=3650), pmin: str = "", pmax: str = ""):
        rule = rule if rule in queries.ANALYSIS_RULES else "PREMATCH"

        def parse(value):
            try:
                return Decimal(value) if value.strip() else None
            except InvalidOperation:
                return None
        conn = open_db()
        try:
            data = queries.threshold_analysis(conn, rule, days, parse(pmin), parse(pmax))
            return render(request, "analysis.html", _conn=conn, data=data, rule=rule, days=days, pmin=pmin, pmax=pmax, page="analysis")
        finally:
            conn.close()

    @app.get("/health")
    def health():
        """Unauthenticated liveness probe; reveals only whether the bot polled recently."""
        conn = queries.connect(database_path)
        if conn is None:
            return {"ok": False, "reason": "no database"}
        try:
            state = queries.bot_state(conn)
            last = state.get("last_live_scan")
            age = ago(last["value"]) if last else None
            return {"ok": age is not None and age < 300, "last_live_scan_seconds_ago": age}
        finally:
            conn.close()

    return app
