# tipster.py (FastAPI) - VERSÃO FULL (CORS + Mercados completos + Preferência de casas)
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import requests, os, time, traceback

# ------------- Config -------------
app = FastAPI(title="Tipster IA - Full API")

# Controle de CORS via env var (DEV=1 -> "*" ; PROD=0 -> lista restrita)
DEBUG_ALLOW_ALL = os.environ.get("ALLOW_ALL_ORIGINS", "1") == "1"

if DEBUG_ALLOW_ALL:
    allow_origins = ["*"]
else:
    allow_origins = [
        "https://jean-rgsocd.github.io",
        "https://analisador-apostas.onrender.com"
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Garantir que até erros retornem com CORS (usa "*" para o handler, ok para debug)
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # log completo no servidor
    print("UNHANDLED EXCEPTION:", traceback.format_exc())
    # devolve JSON com header CORS (usa "*" para compatibilidade)
    headers = {"Access-Control-Allow-Origin": "*" if DEBUG_ALLOW_ALL else (allow_origins[0] if allow_origins else "*")}
    return JSONResponse(status_code=500, content={"detail": "Erro interno no servidor"}, headers=headers)

# health / ping
@app.get("/ping")
def ping():
    return {"message": "pong", "utc": datetime.utcnow().isoformat()}

API_SPORTS_KEY = os.environ.get("API_SPORTS_KEY", "7baa5e00c8ae57d0e6240f790c6840dd")
API_URL_BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_SPORTS_KEY}

PREFERRED_BOOKMAKERS = ["bet365", "betano", "superbet", "pinnacle"]

CACHE_TTL = int(os.environ.get("CACHE_TTL", "60"))  # segundos
_cache: Dict[str, Dict[str, Any]] = {}

# ------------- Cache helpers -------------
def _cache_get(key: str):
    rec = _cache.get(key)
    if not rec:
        return None
    if time.time() - rec.get("ts", 0) > CACHE_TTL:
        _cache.pop(key, None)
        return None
    return rec.get("data")

def _cache_set(key: str, data):
    _cache[key] = {"ts": time.time(), "data": data}

# ------------- HTTP helper -------------
def api_get_raw(path: str, params: dict = None) -> Optional[Dict[str, Any]]:
    """
    Faz GET para API-Sports e retorna parsed JSON ou None.
    Não lança exceção pro chamador — chamador precisa tratar None.
    """
    url = f"{API_URL_BASE}/{path}"
    try:
        r = requests.get(url, headers=HEADERS, params=params or {}, timeout=25)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        # log detalhado para debugging no Render
        print(f"[api_get_raw] ERROR for {url} with params {params}: {e}")
        try:
            # cuidado: r pode não existir
            print("preview:", getattr(r, "text", "")[:400])
        except Exception:
            pass
        print(traceback.format_exc())
        return None

# ------------- Fixtures helpers -------------
def normalize_game(raw: dict) -> dict:
    fixture = raw.get("fixture", {})
    league = raw.get("league", {}) or {}
    teams = raw.get("teams", {}) or {}
    status = fixture.get("status", {}) or {}
    return {
        "game_id": fixture.get("id"),
        "date": fixture.get("date"),
        "league": league,
        "teams": teams,
        "status": status,
        "type": ("live" if status.get("elapsed") else "scheduled"),
        "raw": raw
    }

def get_fixtures_for_dates(days_forward: int = 2) -> List[dict]:
    ck = f"all_fixtures_v4_{days_forward}"
    cached = _cache_get(ck)
    if cached:
        return cached

    dates = [(datetime.utcnow().date() + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_forward + 1)]
    all_fixtures: List[dict] = []
    seen_ids = set()

    live_data = api_get_raw("fixtures", params={"live": "all"})
    if live_data and live_data.get("response"):
        for fixture in live_data["response"]:
            fid = fixture.get("fixture", {}).get("id")
            if fid and fid not in seen_ids:
                all_fixtures.append(normalize_game(fixture))
                seen_ids.add(fid)

    for d in dates:
        fixtures_data = api_get_raw("fixtures", params={"date": d})
        if fixtures_data and fixtures_data.get("response"):
            for fixture in fixtures_data["response"]:
                fid = fixture.get("fixture", {}).get("id")
                if fid and fid not in seen_ids:
                    all_fixtures.append(normalize_game(fixture))
                    seen_ids.add(fid)

    _cache_set(ck, all_fixtures)
    return all_fixtures

# ------------- Listagem endpoints -------------
@app.get("/countries")
def countries():
    games = get_fixtures_for_dates()
    countries_set = {g.get("league", {}).get("country") for g in games if g.get("league", {}).get("country")}
    return sorted([c for c in countries_set if c])

@app.get("/leagues")
def leagues(country: str = Query(...)):
    games = get_fixtures_for_dates()
    league_map = {g.get("league", {}).get("id"): g.get("league") for g in games if g.get("league", {}).get("country") == country}
    return list(league_map.values())

@app.get("/games")
def games(league: int = Query(None)):
    all_games = get_fixtures_for_dates()
    if league:
        return [g for g in all_games if g.get("league", {}).get("id") == int(league)]
    return all_games

# ------------- Stats helpers -------------
def fetch_football_statistics(fixture_id: int) -> Optional[Dict[str, Any]]:
    return api_get_raw("fixtures/statistics", params={"fixture": fixture_id})

def safe_int(v):
    try:
        return int(v)
    except (ValueError, TypeError):
        try:
            return int(float(v))
        except Exception:
            return 0

def build_stats_map(stats_raw: Optional[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    if not stats_raw or "response" not in stats_raw:
        return out
    for item in stats_raw["response"]:
        team = item.get("team") or {}
        tid = team.get("id")
        if not tid:
            continue
        out[tid] = {}
        for s in item.get("statistics", []) or []:
            k = (s.get("type") or s.get("name") or "").strip()
            v = s.get("value")
            if isinstance(v, str) and "/" in v:
                try:
                    v = int(v.split("/")[0])
                except Exception:
                    v = safe_int(v)
            else:
                v = safe_int(v)
            out[tid][k] = v
    return out

# ------------- Heurísticas (mercados completos) -------------
def heuristics_football(fixture_raw: dict, stats_map: Dict[int, Dict[str, Any]]) -> Tuple[List[dict], dict]:
    fixture = fixture_raw
    teams = fixture.get("teams", {}) or {}
    home = teams.get("home", {}) or {}
    away = teams.get("away", {}) or {}
    home_id = home.get("id"); away_id = away.get("id")

    home_stats = stats_map.get(home_id, {}) or {}
    away_stats = stats_map.get(away_id, {}) or {}

    def g(d, *keys):
        for k in keys:
            if k in d:
                return d[k]
        return 0

    h_shots = g(home_stats, "Total Shots", "Shots")
    a_shots = g(away_stats, "Total Shots", "Shots")
    h_sot = g(home_stats, "Shots on Goal", "Shots on Target")
    a_sot = g(away_stats, "Shots on Goal", "Shots on Target")
    h_corners = g(home_stats, "Corners", "Corner Kicks", "Corner Kicks 1H")
    a_corners = g(away_stats, "Corners", "Corner Kicks", "Corner Kicks 1H")
    h_pos = g(home_stats, "Ball Possession", "Possession")
    a_pos = g(away_stats, "Ball Possession", "Possession")
    h_fouls = g(home_stats, "Fouls")
    a_fouls = g(away_stats, "Fouls")

    h_attacks = g(home_stats, "Attacks", "Attacks")
    a_attacks = g(away_stats, "Attacks", "Attacks")
    h_danger = g(home_stats, "Dangerous Attacks", "Dangerous Attacks")
    a_danger = g(away_stats, "Dangerous Attacks", "Dangerous Attacks")

    def norm_pos(x):
        if isinstance(x, str) and "%" in x:
            try:
                return int(x.replace("%", "").strip())
            except:
                return 50
        try:
            return int(x)
        except:
            return 50

    h_pos = norm_pos(h_pos); a_pos = norm_pos(a_pos)

    h_power = (h_sot * 1.6) + (h_shots * 0.6) + (h_corners * 0.35) + (h_pos * 0.2) - (h_fouls * 0.1)
    a_power = (a_sot * 1.6) + (a_shots * 0.6) + (a_corners * 0.35) + (a_pos * 0.2) - (a_fouls * 0.1)
    power_diff = h_power - a_power

    goals = fixture.get("goals", {}) or {}
    h_goals = safe_int(goals.get("home"))
    a_goals = safe_int(goals.get("away"))
    total_goals = h_goals + a_goals

    preds: List[dict] = []

    def add(market, rec, conf, reason=None):
        item = {"market": market, "recommendation": rec, "confidence": round(conf, 2)}
        if reason:
            item["reason"] = reason
        preds.append(item)

    # Detecta se é pré-jogo (sem minutos corridos)
    is_pregame = not bool(fixture.get("status", {}).get("elapsed"))

    # --- Heurísticas principais ---
    # Power-based strong picks
    if power_diff > 6:
        add("moneyline", "Vitória Casa", 0.85, f"Power diff {power_diff:.1f}")
        add("dnb", "Casa (DNB)", 0.7)
        add("double_chance", "Casa ou Empate", 0.6)
    elif power_diff < -6:
        add("moneyline", "Vitória Visitante", 0.85, f"Power diff {power_diff:.1f}")
        add("dnb", "Fora (DNB)", 0.7)
        add("double_chance", "Fora ou Empate", 0.6)
    else:
        add("moneyline", "Sem favorito definido", 0.35)
        pos_diff = abs(h_pos - a_pos)
        danger_diff = abs(safe_int(h_danger) - safe_int(a_danger))
        attacks_diff = abs(safe_int(h_attacks) - safe_int(a_attacks))
        balanced = (abs(power_diff) < 1 and pos_diff <= 3 and danger_diff <= 1 and attacks_diff <= 2)
        if not balanced:
            if (h_pos > a_pos) or (safe_int(h_danger) > safe_int(a_danger)) or (safe_int(h_attacks) > safe_int(a_attacks)):
                add("double_chance", "Casa ou Empate", 0.5)
            else:
                add("double_chance", "Fora ou Empate", 0.5)

    # DNB mid strength if power difference noticeable
    if abs(power_diff) >= 3:
        if power_diff > 0:
            add("dnb", "Casa (DNB)", 0.65)
        else:
            add("dnb", "Fora (DNB)", 0.65)

    # --- Goals / Over-Under ---
    combined_sot = h_sot + a_sot
    combined_shots = h_shots + a_shots

    if is_pregame:
        # Pré-jogo -> tendências médias
        add("over_1_5", "OVER 1.5", 0.65, "Tendência histórica de gols")
        add("over_2_5", "OVER 2.5", 0.55, "Probabilidade média pré-jogo")
        add("btts", "SIM", 0.55, "Ambas marcam comum em pré-jogo equilibrado")
    else:
        if combined_sot >= 4 or combined_shots >= 12:
            add("over_2_5", "OVER 2.5", 0.75, f"SOT {combined_sot}, shots {combined_shots}")
        else:
            add("over_2_5", "UNDER 2.5", 0.45)

        if combined_shots >= 8 or combined_sot >= 3:
            add("over_1_5", "OVER 1.5", 0.7)
        else:
            add("over_1_5", "UNDER 1.5", 0.4)

        if combined_sot >= 7 or combined_shots >= 18:
            add("over_3_5", "OVER 3.5", 0.65)
        else:
            add("over_3_5", "UNDER 3.5", 0.45)

        # Both teams to score
        if h_sot >= 2 and a_sot >= 2:
            add("btts", "SIM", 0.8)
        elif (h_shots >= 5 and a_shots >= 3) or (a_shots >= 5 and h_shots >= 3):
            add("btts", "SIM", 0.6)
        else:
            add("btts", "NAO", 0.45)

    # --- Half time goals / corners ---
    h_corners_ht = g(home_stats, "Corners 1st Half", "Corners Half Time", "Corner Kicks 1H")
    a_corners_ht = g(away_stats, "Corners 1st Half", "Corners Half Time", "Corner Kicks 1H")
    combined_corners_ht = safe_int(h_corners_ht) + safe_int(a_corners_ht)

    if combined_corners_ht >= 3:
        add("corners_ht_over", "OVER 4.5", 0.65, f"1H corners {combined_corners_ht}")

    if (safe_int(h_shots) + safe_int(a_shots)) >= 5:
        add("over_2_5_ht", "OVER 1.0", 0.6)

    # --- Asian & European handicaps ---
    if power_diff >= 5:
        add("asian_handicap_home", f"{home.get('name')} -1.0", 0.7)
    elif power_diff >= 3:
        add("asian_handicap_home", f"{home.get('name')} -0.5", 0.6)

    if power_diff <= -5:
        add("asian_handicap_away", f"{away.get('name')} -1.0", 0.7)
    elif power_diff <= -3:
        add("asian_handicap_away", f"{away.get('name')} -0.5", 0.6)

    if power_diff >= 4:
        add("handicap_european", f"{home.get('name')} -1", 0.6)
    elif power_diff <= -4:
        add("handicap_european", f"{away.get('name')} -1", 0.6)

    # HT/FT suggestions
    if power_diff > 6 and total_goals == 0:
        add("ht_ft", f"{home.get('name')} / {home.get('name')}", 0.7)
    elif power_diff < -6 and total_goals == 0:
        add("ht_ft", f"{away.get('name')} / {away.get('name')}", 0.7)

    # --- Corners fulltime ---
    total_corners = safe_int(h_corners) + safe_int(a_corners)
    if is_pregame:
        add("corners_ft_over", "OVER 9.5", 0.6, "Média histórica de escanteios")
    else:
        if total_corners >= 7:
            add("corners_ft_over", "OVER 9.5", 0.7, f"Corners {total_corners}")
        else:
            add("corners_ft_under", "UNDER 9.5", 0.45)

    # Asian corners
    if (safe_int(h_corners) - safe_int(a_corners)) >= 2:
        add("corners_asian_ft", f"{home.get('name')} -1.5", 0.65)
    elif (safe_int(a_corners) - safe_int(h_corners)) >= 2:
        add("corners_asian_ft", f"{away.get('name')} -1.5", 0.65)

    # --- Cards ---
    h_yellow = g(home_stats, "Yellow Cards")
    a_yellow = g(away_stats, "Yellow Cards")
    total_cards = safe_int(h_yellow) + safe_int(a_yellow)

    if is_pregame:
        add("cards_over", "OVER 3.5", 0.55, "Tendência média de cartões pré-jogo")
    else:
        if total_cards >= 2:
            add("cards_over", "OVER 3.5", 0.6)
        else:
            add("cards_over", "UNDER 3.5", 0.45)

    # --- Summary ---
    summary = {
        "home_team": home.get("name"),
        "away_team": away.get("name"),
        "home_power": round(h_power, 2),
        "away_power": round(a_power, 2),
        "combined_shots": combined_shots,
        "combined_sot": combined_sot,
        "combined_corners": total_corners,
        "total_goals": total_goals
    }

    # Deduplica previsões
    seen = set()
    deduped_preds = []
    for p in preds:
        key = (p.get("market"), p.get("recommendation"))
        if key not in seen:
            deduped_preds.append(p)
            seen.add(key)

    # Ordena pela confiança
    deduped_preds.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    return deduped_preds, summary

# ------------- Odds helpers -------------
def build_book_odds_map(bookmaker: dict) -> Dict[Tuple[str, str], float]:
    out: Dict[Tuple[str, str], float] = {}
    if not bookmaker:
        return out
    for bet in bookmaker.get("bets", []) or []:
        bet_name = bet.get("name") or ""
        for val in bet.get("values", []) or []:
            # ✅ garante que value sempre será string antes do strip
            raw_val = val.get("value")
            v = str(raw_val).strip() if raw_val is not None else ""
            
            odd = val.get("odd")
            try:
                odd_f = float(odd)
            except Exception:
                try:
                    odd_f = float(str(odd).replace(',', '.'))
                except Exception:
                    odd_f = 0.0
            out[(bet_name.strip(), v)] = odd_f
    return out

def enhance_predictions_with_preferred_odds(predictions: List[Dict], odds_raw: Optional[Dict]) -> List[Dict]:
    """
    Para cada predição, busca odds nas casas preferidas e anexa best_odd & bookmaker.
    Remove duplicados exatos (mesmo mercado + mesma recomendação).
    Ordena pela confiança final (desc).
    """
    if not odds_raw or not odds_raw.get("response"):
        return predictions

    try:
        bookmakers = odds_raw["response"][0].get("bookmakers", []) or []
    except Exception:
        bookmakers = []

    # filtra só casas preferidas
    preferred_books = []
    for b in bookmakers:
        name = (b.get("name") or "").lower()
        if any(pref in name for pref in PREFERRED_BOOKMAKERS):
            preferred_books.append(b)

    if not preferred_books:
        return predictions

    # mapeamento dos mercados internos para os nomes da API
    def conv_contains(rec: str, *opts):
        if not rec:
            return None
        r = str(rec).lower()
        for o in opts:
            if o.lower() in r:
                return True
        return False

    market_map = {
        "moneyline": {
            "names": ["Match Winner", "Match Result", "1X2"],
            "convert": lambda rec: ("Home" if conv_contains(rec, "casa", "home", "vitória casa", "1") else ("Away" if conv_contains(rec, "fora", "away", "vitória visitante", "2") else None))
        },
        "dnb": {
            "names": ["Draw No Bet", "Draw No Bet FT", "DNB"],
            "convert": lambda rec: ("Home" if conv_contains(rec, "casa", "home") else ("Away" if conv_contains(rec, "fora", "away") else None))
        },
        "double_chance": {
            "names": ["Double Chance"],
            "convert": lambda rec: ("Home/Draw" if conv_contains(rec, "casa") or conv_contains(rec, "home") or "casa ou empate" in str(rec).lower() else ("Away/Draw" if conv_contains(rec, "fora") or conv_contains(rec, "away") or "fora ou empate" in str(rec).lower() else None))
        },
        "over_2_5": {"names": ["Goals Over/Under", "Over/Under"], "convert": lambda rec: ("Over 2.5" if "over" in str(rec).lower() or "over 2.5" in str(rec).lower() or "over2.5" in str(rec).lower() else ("Under 2.5" if "under" in str(rec).lower() else None))},
        "over_1_5": {"names": ["Goals Over/Under", "Over/Under"], "convert": lambda rec: ("Over 1.5" if "over 1.5" in str(rec).lower() or "over" in str(rec).lower() and "1.5" in str(rec) else ("Under 1.5" if "under" in str(rec).lower() else None))},
        "over_3_5": {"names": ["Goals Over/Under", "Over/Under"], "convert": lambda rec: ("Over 3.5" if "over" in str(rec).lower() and "3.5" in str(rec) else ("Under 3.5" if "under" in str(rec).lower() else None))},
        "over_2_5_ht": {"names": ["Half-time Goals Over/Under", "Goals Over/Under - 1st Half"], "convert": lambda rec: ("Over 1.0" if "over" in str(rec).lower() else ("Under 1.0" if "under" in str(rec).lower() else None))},
        "btts": {"names": ["Both Teams To Score", "Both Teams To Score?"], "convert": lambda rec: ("Yes" if conv_contains(rec, "sim", "yes") else ("No" if conv_contains(rec, "não", "nao", "no") else None))},
        "asian_handicap_home": {"names": ["Asian Handicap", "Asian Handicap Match"], "convert": lambda rec: (str(rec).split()[-1] if "-" in str(rec) else None)},
        "asian_handicap_away": {"names": ["Asian Handicap", "Asian Handicap Match"], "convert": lambda rec: (str(rec).split()[-1] if "-" in str(rec) else None)},
        "handicap_european": {"names": ["European Handicap", "Handicap"], "convert": lambda rec: (str(rec).split()[-1] if "-" in str(rec) else None)},
        "ht_ft": {"names": ["Half Time / Full Time", "HT/FT"], "convert": lambda rec: rec},
        "corners_ft_over": {"names": ["Corners Over/Under", "Corners Total"], "convert": lambda rec: ("Over 9.5" if "over" in str(rec).lower() and "9.5" in str(rec) else ("Under 9.5" if "under" in str(rec).lower() else None))},
        "corners_ft_under": {"names": ["Corners Over/Under", "Corners Total"], "convert": lambda rec: ("Under 9.5" if "under" in str(rec).lower() and "9.5" in str(rec) else ("Over 9.5" if "over" in str(rec).lower() else None))},
        "corners_ht_over": {"names": ["1st Half Corners", "Corners Over/Under - 1st Half"], "convert": lambda rec: ("Over 4.5" if "over" in str(rec).lower() and "4.5" in str(rec) else None)},
        "corners_asian_ft": {"names": ["Asian Corners", "Corners Asian Handicap"], "convert": lambda rec: (str(rec).split()[-1] if "-" in str(rec) else None)},
        "cards_over": {"names": ["Cards Over/Under", "Total Cards"], "convert": lambda rec: ("Over 3.5" if "over" in str(rec).lower() else ("Under 3.5" if "under" in str(rec).lower() else None))}
    }

    enhanced = []
    for pred in predictions:
        market = pred.get("market")
        rec = pred.get("recommendation")
        mapping = market_map.get(market)
        if not mapping:
            enhanced.append(pred)
            continue

        try:
            api_val = mapping["convert"](rec)
        except Exception:
            api_val = None
        if not api_val:
            enhanced.append(pred)
            continue

        best_odd = 0.0
        best_book = None
        best_market_name = None

        # checa odds nos books preferidos
        for book in preferred_books:
            book_map = build_book_odds_map(book)
            for name in mapping.get("names", []):
                odd = book_map.get((name, api_val), 0.0)
                if odd and odd > best_odd:
                    best_odd = odd
                    best_book = book.get("name")
                    best_market_name = name

        if best_odd > 0:
            p = dict(pred)
            p["best_odd"] = best_odd
            p["bookmaker"] = best_book
            p["market_name_found"] = best_market_name
            enhanced.append(p)
        else:
            enhanced.append(pred)

        # 🔹 remove duplicados exatos (mesmo mercado + mesma recomendação)
    seen = set()
    deduped = []
    for p in enhanced:
        key = (p.get("market"), p.get("recommendation"))
        if key not in seen:
            deduped.append(p)
            seen.add(key)

    # 🔹 ordena pela confiança (descendente)
    deduped.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    return deduped

# ------------- Analyze endpoint -------------
@app.get("/analyze")
def analyze(game_id: int = Query(...)):
    print(f"[ANALYZE] Chamado com game_id={game_id}")  # log para debug

    # fixtures
    fixture_data = api_get_raw("fixtures", params={"id": game_id})
    if fixture_data is None:
        raise HTTPException(status_code=502, detail="Erro ao consultar API externa (fixtures)")

    if not fixture_data.get("response"):
        raise HTTPException(status_code=404, detail=f"Jogo {game_id} não encontrado")

    fixture = fixture_data["response"][0]

    # stats
    stats_raw = fetch_football_statistics(game_id)
    if stats_raw is None:
        print(f"[analyze] warning: stats fetch returned None for fixture {game_id}")
        stats_map = {}
    else:
        stats_map = build_stats_map(stats_raw)

    # heurísticas
    preds, summary = heuristics_football(fixture, stats_map)

    # odds
    odds_raw = api_get_raw("odds", params={"fixture": game_id})
    enhanced = enhance_predictions_with_preferred_odds(preds, odds_raw)

    # top 3 picks (destacados no front)
    top3 = enhanced[:3]

    return {
        "game_id": game_id,
        "summary": summary,
        "predictions": enhanced,
        "top3": top3,
        "raw_fixture": fixture,
        "raw_stats": stats_raw,
        "raw_odds": odds_raw
    }

# ------------- Health endpoint -------------
@app.get("/health")
def health():
    return {"status": "ok", "utc": datetime.utcnow().isoformat()}

