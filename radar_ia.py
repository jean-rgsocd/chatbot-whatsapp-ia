# radar_ia.py
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import requests, os, traceback, time

app = FastAPI(title="Radar IA - Futebol")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

API_KEY = os.environ.get("API_SPORTS_KEY", "7baa5e00c8ae57d0e6240f790c6840dd")
API_CFG = {"football": {"base": "https://v3.football.api-sports.io", "host": "v3.football.api-sports.io"}}

CACHE_TTL = 8
_cache = {}

# --------------------------
# Helpers
# --------------------------
def _cache_get(key):
    rec = _cache.get(key)
    if not rec:
        return None
    if time.time() - rec["ts"] > CACHE_TTL:
        _cache.pop(key, None)
        return None
    return rec["data"]

def _cache_set(key, data):
    _cache[key] = {"ts": time.time(), "data": data}

def headers_for():
    cfg = API_CFG["football"]
    return {"x-rapidapi-key": API_KEY, "x-rapidapi-host": cfg["host"]}

def safe_get(url, headers, params=None, timeout=20):
    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("safe_get error", url, params, e)
        return None

def _compute_sort_key(ev):
    try:
        elapsed = int(ev.get("time", {}).get("elapsed") or 0)
    except:
        elapsed = 0
    try:
        second = int(ev.get("time", {}).get("second") or 0)
    except:
        second = 0
    try:
        extra = int(ev.get("time", {}).get("extra") or 0)
    except:
        extra = 0
    return (elapsed + extra) * 60 + second

def _format_display_time(ev):
    t = ev.get("time", {}) or {}
    elapsed = t.get("elapsed")
    second = t.get("second")
    extra = t.get("extra")
    if elapsed is None:
        return "-"
    sec_part = f'{int(second):02d}"' if second is not None else ""
    if extra:
        return f"{elapsed}+{extra}'{sec_part}"
    return f"{elapsed}'{sec_part}"

def classify_event(ev):
    t = (ev.get("type") or "").lower()
    d = (ev.get("detail") or "").lower()
    if "goal" in t or "goal" in d: return "Goal"
    if "card" in t or "card" in d:
        if "yellow" in d: return "Yellow Card"
        if "red" in d: return "Red Card"
        return "Card"
    if "substitution" in t or "sub" in d: return "Substitution"
    if "corner" in t or "corner" in d: return "Corner"
    if "foul" in t or "foul" in d: return "Foul"
    if "shot" in t or "shot" in d or "on target" in d: return "Shot"
    if "var" in t or "var" in d: return "VAR"
    return ev.get("type") or ev.get("detail") or "Other"

def try_int(v):
    try:
        if v is None: return None
        if isinstance(v, str) and "%" in v:
            return int(v.replace("%", "").strip())
        return int(v)
    except:
        try:
            return int(float(v))
        except:
            return v

# --------------------------
# Routes
# --------------------------
@app.get("/ligas")
def ligas():
    ck = "radar_ligas_live"
    c = _cache_get(ck)
    if c is not None:
        return c
    cfg = API_CFG["football"]
    resp = safe_get(f"{cfg['base']}/fixtures", headers_for(), params={"live": "all"})
    if not resp:
        return []
    data = resp.get("response", [])
    leagues = {}
    for f in data:
        l = f.get("league") or {}
        leagues[l.get("id")] = {"id": l.get("id"), "name": l.get("name"), "country": l.get("country")}
    out = list(leagues.values())
    _cache_set(ck, out)
    return out

@app.get("/jogos-aovivo")
def jogos_aovivo(league: int = Query(None)):
    ck = f"radar_jogos_live_{league or 'all'}"
    c = _cache_get(ck)
    if c is not None:
        return c
    cfg = API_CFG["football"]
    params = {"live": "all"}
    if league:
        params["league"] = league
    resp = safe_get(f"{cfg['base']}/fixtures", headers_for(), params=params)
    if not resp:
        return []
    data = resp.get("response", [])
    out = []
    for f in data:
        out.append({
            "game_id": f.get("fixture", {}).get("id"),
            "title": f"{f.get('teams', {}).get('home', {}).get('name')} vs {f.get('teams', {}).get('away', {}).get('name')} ({f.get('league', {}).get('name')})",
            "league": f.get("league"),
            "teams": f.get("teams"),
            "fixture": f.get("fixture"),
            "status": f.get("fixture", {}).get("status")
        })
    _cache_set(ck, out)
    return out

@app.get("/stats-aovivo/{game_id}")
def stats_aovivo(game_id: int, half: bool = Query(False)):
    ck = f"radar_stats_{game_id}_{'half' if half else 'full'}"
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    try:
        base = API_CFG["football"]["base"]
        headers = headers_for()

        # Fixture
        fixture_resp = safe_get(f"{base}/fixtures", headers, params={"id": game_id})
        if not fixture_resp:
            raise HTTPException(status_code=404, detail="Fixture not found")
        fixture_data = fixture_resp.get("response", [])
        fixture = fixture_data[0] if fixture_data else {}

        home_id = fixture.get("teams", {}).get("home", {}).get("id")
        away_id = fixture.get("teams", {}).get("away", {}).get("id")

        # Stats (full)
        stats_resp = safe_get(f"{base}/fixtures/statistics", headers, params={"fixture": game_id})
        full_stats = {"home": {}, "away": {}}
        try:
            stats_list = stats_resp.get("response", [])
            for team_stats in stats_list:
                team = team_stats.get("team") or {}
                tid = team.get("id")
                side = "home" if tid == home_id else ("away" if tid == away_id else None)
                tmp = {}

                for s in (team_stats.get("statistics") or []):
                    k = (s.get("type") or s.get("name") or "").strip().lower()
                    v = try_int(s.get("value"))

                    if "total shots" in k:
                        tmp["total_shots"] = v
                    elif "shots on goal" in k:
                        tmp["shots_on_goal"] = v
                    elif "shots off goal" in k:
                        tmp["shots_off_goal"] = v
                    elif "blocked shots" in k:
                        tmp["shots_blocked"] = v
                    elif "ball possession" in k:
                        tmp["possession"] = v
                    elif "corner" in k:
                        tmp["corners"] = v
                    elif "foul" in k:
                        tmp["fouls"] = v
                    elif "yellow" in k:
                        tmp["yellow_cards"] = v
                    elif "red" in k:
                        tmp["red_cards"] = v
                    elif "offside" in k or "offsides" in k:
                        tmp["offsides"] = v
                    else:
                        tmp[k] = v

                if side:
                    full_stats[side].update(tmp)
        except Exception as e:
            print("parse stats error", e)

        # Events
        events_resp = safe_get(f"{base}/fixtures/events", headers, params={"fixture": game_id})
        events = events_resp.get("response", []) if events_resp else []
        processed = []
        for ev in events:
            processed.append({
                "display_time": _format_display_time(ev),
                "category": classify_event(ev),
                "type": ev.get("type"),
                "detail": ev.get("detail"),
                "player": ev.get("player", {}).get("name") if ev.get("player") else None,
                "team": ev.get("team", {}).get("name") if ev.get("team") else None,
                "raw": ev,
                "_sort": _compute_sort_key(ev)
            })
        processed.sort(key=lambda x: x["_sort"], reverse=True)

        # Period stats
        period_agg = events_to_period_stats(events, home_id, away_id)

        # Preencher stats ausentes com fallback de eventos
        for side in ("home", "away"):
            if not full_stats.get(side):
                full_stats[side] = {}
            if full_stats[side].get("total_shots") in (None, 0):
                full_stats[side]["total_shots"] = period_agg["full"][side].get("shots", 0)
            if full_stats[side].get("shots_on_goal") in (None, 0):
                full_stats[side]["shots_on_goal"] = period_agg["full"][side].get("shots_on_target", 0)
            if full_stats[side].get("shots_off_goal") in (None, 0):
                full_stats[side]["shots_off_goal"] = period_agg["full"][side].get("shots_off_goal", 0)
            if full_stats[side].get("shots_blocked") in (None, 0):
                full_stats[side]["shots_blocked"] = period_agg["full"][side].get("shots_blocked", 0)
            if full_stats[side].get("corners") in (None, 0):
                full_stats[side]["corners"] = period_agg["full"][side].get("corners", 0)
            if full_stats[side].get("fouls") in (None, 0):
                full_stats[side]["fouls"] = period_agg["full"][side].get("fouls", 0)
            if full_stats[side].get("yellow_cards") in (None, 0):
                full_stats[side]["yellow_cards"] = period_agg["full"][side].get("yellow", 0)
            if full_stats[side].get("red_cards") in (None, 0):
                full_stats[side]["red_cards"] = period_agg["full"][side].get("red", 0)

        # Escolher stats finais
        statistics = full_stats
        if half:
            statistics = period_agg.get("first") or {"home": {}, "away": {}}

        # Estimativa de acréscimos
        estimated_extra = None
        try:
            elapsed = fixture.get("fixture", {}).get("status", {}).get("elapsed")
            if elapsed is not None:
                recent_count = 0
                for p in processed:
                    rv = p.get("raw", {})
                    et = rv.get("time", {}).get("elapsed") or 0
                    if (int(elapsed) - int(et)) <= 20:
                        cat = p.get("category", "").lower()
                        if "substitution" in cat or "sub" in cat: recent_count += 1
                        if "card" in cat: recent_count += 1
                        if "injury" in cat: recent_count += 2
                minutes = round(recent_count * 0.8) if recent_count > 0 else None
                if not minutes:
                    if 35 <= int(elapsed) <= 45:
                        minutes = 3
                    elif 80 <= int(elapsed) <= 90:
                        minutes = 4
                if minutes:
                    estimated_extra = max(1, min(7, minutes))
        except:
            estimated_extra = None

        result = {
            "fixture": fixture,
            "teams": fixture.get("teams", {}),
            "score": fixture.get("goals") or fixture.get("score") or {},
            "status": fixture.get("status") or fixture.get("fixture", {}).get("status", {}),
            "statistics": statistics,
            "events": processed,
            "estimated_extra": estimated_extra
        }

        _cache_set(ck, result)
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# --------------------------
# Aggregation por período
# --------------------------
def events_to_period_stats(events, home_id, away_id):
    agg = {
        "first": {"home": {"shots": 0, "shots_on_target": 0, "shots_off_goal": 0, "shots_blocked": 0,
                           "corners": 0, "fouls": 0, "yellow": 0, "red": 0},
                  "away": {"shots": 0, "shots_on_target": 0, "shots_off_goal": 0, "shots_blocked": 0,
                           "corners": 0, "fouls": 0, "yellow": 0, "red": 0}},
        "second": {"home": {"shots": 0, "shots_on_target": 0, "shots_off_goal": 0, "shots_blocked": 0,
                            "corners": 0, "fouls": 0, "yellow": 0, "red": 0},
                   "away": {"shots": 0, "shots_on_target": 0, "shots_off_goal": 0, "shots_blocked": 0,
                            "corners": 0, "fouls": 0, "yellow": 0, "red": 0}},
        "full": {"home": {"shots": 0, "shots_on_target": 0, "shots_off_goal": 0, "shots_blocked": 0,
                          "corners": 0, "fouls": 0, "yellow": 0, "red": 0},
                 "away": {"shots": 0, "shots_on_target": 0, "shots_off_goal": 0, "shots_blocked": 0,
                          "corners": 0, "fouls": 0, "yellow": 0, "red": 0}}
    }
    for ev in events:
        team = ev.get("team") or {}
        team_id = team.get("id")
        side = "home" if team_id == home_id else "away"
        elapsed = ev.get("time", {}).get("elapsed")
        period = "full"
        try:
            if elapsed is not None:
                e = int(elapsed)
                period = "first" if e <= 45 else "second"
        except:
            period = "full"
        typ = (ev.get("type") or "").lower()
        detail = (ev.get("detail") or "").lower()
        if "shot" in typ or "shot" in detail or "goal" in typ or "goal" in detail:
            agg[period][side]["shots"] += 1; agg["full"][side]["shots"] += 1
            if "on target" in detail or "goal" in typ or "goal" in detail:
                agg[period][side]["shots_on_target"] += 1; agg["full"][side]["shots_on_target"] += 1
            if "off target" in detail:
                agg[period][side]["shots_off_goal"] += 1; agg["full"][side]["shots_off_goal"] += 1
            if "blocked" in detail:
                agg[period][side]["shots_blocked"] += 1; agg["full"][side]["shots_blocked"] += 1
        if "corner" in typ or "corner" in detail:
            agg[period][side]["corners"] += 1; agg["full"][side]["corners"] += 1
        if "foul" in typ or "foul" in detail:
            agg[period][side]["fouls"] += 1; agg["full"][side]["fouls"] += 1
        if "card" in typ or "yellow" in detail or "red" in detail:
            if "red" in detail:
                agg[period][side]["red"] += 1; agg["full"][side]["red"] += 1
            else:
                agg[period][side]["yellow"] += 1; agg["full"][side]["yellow"] += 1
    return agg
