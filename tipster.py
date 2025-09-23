# tipster.py — UNIFICADO (sports_betting_analyzer + radar_ia + opta_ia + Flask API)
# Mantive toda a lógica de análise e helpers (cache, heurísticas, odds mapping, análise ao vivo, análise de jogadores).
import os
import time
import traceback
import requests
from datetime import datetime, date, timedelta
from collections import defaultdict
from typing import Dict, Any, List, Optional, Tuple
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# Carrega .env se existir
load_dotenv()

# =========================
# CONFIGURAÇÃO GERAL / API-FOOTBALL
# =========================
API_SPORTS_KEY = os.environ.get("API_SPORTS_KEY")
if not API_SPORTS_KEY:
    # não raise pra permitir debug local, mas loga
    print("WARNING: API_SPORTS_KEY não definida — muitas rotas vão falhar sem a chave.")

API_URL_BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_SPORTS_KEY} if API_SPORTS_KEY else {}

# =========================
# CACHE GERAL (usado por módulos)
# =========================
CACHE_TTL = int(os.environ.get("CACHE_TTL", "60"))
_global_cache: Dict[str, Dict[str, Any]] = {}

def _cache_get(key: str):
    rec = _global_cache.get(key)
    if not rec:
        return None
    if time.time() - rec.get("ts", 0) > CACHE_TTL:
        _global_cache.pop(key, None)
        return None
    return rec.get("data")

def _cache_set(key: str, data):
    _global_cache[key] = {"ts": time.time(), "data": data}

# =========================
# HTTP HELPERS (reaproveitados dos módulos)
# =========================
def api_get_raw(path: str, params: dict = None, timeout: int = 25) -> Optional[Dict[str, Any]]:
    """Faz GET para API-Sports e retorna parsed JSON ou None."""
    if not API_SPORTS_KEY:
        print("ERRO: A variável de ambiente API_SPORTS_KEY não está definida.")
        return None
    url = f"{API_URL_BASE}/{path}"
    try:
        r = requests.get(url, headers=HEADERS, params=params or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"api_get_raw ERROR {url} params={params}: {e}")
        return None

# =========================
# SPORTS BETTING ANALYZER (pré-live)
# =========================

PREFERRED_BOOKMAKERS = ["bet365", "betano", "superbet", "pinnacle"]

def safe_int(v):
    try: return int(v)
    except (ValueError, TypeError):
        try: return int(float(v))
        except Exception: return 0

def safe_float(v, default: float = 0.0):
    try:
        return float(str(v).replace(",", "."))
    except Exception:
        return default

def normalize_game(raw: dict) -> dict:
    fixture = raw.get("fixture", {}) or {}
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

def get_fixtures_for_dates(days_forward: int = 0) -> List[dict]:
    """
    Retorna fixtures cobrindo hoje + days_forward dias e jogos ao vivo.
    Mantém cache para economizar chamadas.
    """
    ck = f"all_fixtures_v4_{days_forward}"
    cached = _cache_get(ck)
    if cached:
        return cached

    dates = [(datetime.utcnow().date() + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_forward + 1)]
    all_fixtures: List[dict] = []
    seen_ids = set()

    # primeiro adiciona live
    live_data = api_get_raw("fixtures", params={"live": "all"})
    if live_data and live_data.get("response"):
        for fixture in live_data["response"]:
            fid = fixture.get("fixture", {}).get("id")
            if fid and fid not in seen_ids:
                all_fixtures.append(normalize_game(fixture))
                seen_ids.add(fid)

    # depois datas
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

def fetch_football_statistics(fixture_id: int) -> Optional[Dict[str, Any]]:
    return api_get_raw("fixtures/statistics", params={"fixture": fixture_id})

def build_stats_map(stats_raw: Optional[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    if not stats_raw or "response" not in stats_raw:
        return out
    for item in stats_raw["response"]:
        tid = item.get("team", {}).get("id")
        if not tid:
            continue
        out[tid] = {}
        for s in item.get("statistics", []) or []:
            k = (s.get("type") or "").strip()
            v = s.get("value")
            out[tid][k] = safe_int(v)
    return out

def heuristics_football(fixture_raw: dict, stats_map: Dict[int, Dict[str, Any]]) -> Tuple[List[dict], dict]:
    teams = fixture_raw.get("teams", {})
    home = teams.get("home", {}); away = teams.get("away", {})
    home_stats = stats_map.get(home.get("id"), {}); away_stats = stats_map.get(away.get("id"), {})
    def g(d, k): return d.get(k, 0)
    h_sot = g(home_stats, "Shots on Goal"); a_sot = g(away_stats, "Shots on Goal")
    h_power = h_sot * 1.6; a_power = a_sot * 1.6
    power_diff = h_power - a_power
    preds: List[dict] = []
    def add(market, rec, conf): preds.append({"market": market, "recommendation": rec, "confidence": conf})
    if power_diff > 4: add("moneyline", "Vitória Casa", 0.8)
    elif power_diff < -4: add("moneyline", "Vitória Visitante", 0.8)
    summary = {"home_power": round(h_power, 2), "away_power": round(a_power, 2)}
    return preds, summary

def enhance_predictions_with_preferred_odds(predictions: List[Dict], odds_raw: Optional[Dict]) -> List[Dict]:
    if not odds_raw or not odds_raw.get("response"):
        return predictions
    for pred in predictions:
        pred["best_book"] = None
        pred["best_odd"] = None
    return predictions

def analyze(game_id: int):
    fixture_data = api_get_raw("fixtures", params={"id": game_id})
    if not fixture_data or not fixture_data.get("response"):
        return None
    fixture = fixture_data["response"][0]
    stats_raw = fetch_football_statistics(game_id)
    stats_map = build_stats_map(stats_raw)
    preds, summary = heuristics_football(fixture, stats_map)
    odds_raw = api_get_raw("odds", params={"fixture": game_id})
    enhanced = enhance_predictions_with_preferred_odds(preds, odds_raw)
    return {
        "game_id": game_id,
        "summary": summary,
        "predictions": enhanced,
        "top3": enhanced[:3],
        "raw_fixture": fixture
    }

def analyze_game(game_id: int):
    return analyze(game_id)

# =========================
# análise a partir de dados ao vivo (RadarIA)
# =========================
def analyze_live_from_stats(radar_data: Dict) -> List[Dict]:
    if not radar_data:
        return []

    tips = []
    stats = radar_data.get("statistics", {})
    home_stats = stats.get("home", {})
    away_stats = stats.get("away", {})
    status = radar_data.get("status", {})
    score = radar_data.get("score", {}).get("fulltime", {})

    elapsed = status.get("elapsed", 0)
    home_goals = score.get("home", 0)
    away_goals = score.get("away", 0)
    total_goals = home_goals + away_goals

    home_shots = home_stats.get('total_shots', 0)
    away_shots = away_stats.get('total_shots', 0)
    total_shots = home_shots + away_shots

    home_corners = home_stats.get('corner_kicks', 0)
    away_corners = away_stats.get('corner_kicks', 0)
    total_corners = home_corners + away_corners

    def add_tip(market, recommendation, reason, confidence):
        tips.append({
            "market": market,
            "recommendation": recommendation,
            "reason": reason,
            "confidence": confidence
        })

    if elapsed > 20:
        if total_shots > 7 and total_goals < 2:
            add_tip("Gols Asiáticos", f"Mais de {total_goals + 0.5}", f"{total_shots} chutes totais", 0.70)
        elif total_shots < 3:
            add_tip("Gols Asiáticos", f"Menos de {total_goals + 1.5}", f"Apenas {total_shots} chutes", 0.65)

    if home_shots > 3 and away_shots > 3 and total_goals < 3:
        add_tip("Ambas Marcam", "Sim", f"Ambos os times chutando ({home_shots} vs {away_shots})", 0.75)

    if elapsed > 25:
        if total_corners > 5:
            add_tip("Escanteios Asiáticos", f"Mais de {total_corners + 2}", f"{total_corners} escanteios já cobrados", 0.80)
        elif total_shots > 10 and total_corners < 4:
            add_tip("Escanteios (Equipe)", "Próximo escanteio para o time mais ofensivo", "Alta pressão, poucos cantos", 0.60)

    if elapsed > 75:
        if total_goals == 0:
            add_tip("Total de Gols", "Menos de 1.5", "Poucos gols e pouco tempo restante", 0.85)
        elif home_goals > away_goals:
            add_tip("Resultado Final", "Vitória do Time da Casa", "Time da casa segurando o resultado", 0.70)

    return tips

# =========================
# RADAR IA (ao vivo) — funções consolidadas
# =========================
RADAR_CACHE_TTL = int(os.environ.get("RADAR_CACHE_TTL", "8"))
_radar_cache: Dict[str, Dict[str, Any]] = {}

API_CFG = {"football": {"base": API_URL_BASE, "host": "v3.football.api-sports.io"}}

def _radar_cache_get(key):
    rec = _radar_cache.get(key)
    if not rec:
        return None
    if time.time() - rec.get("ts", 0) > RADAR_CACHE_TTL:
        _radar_cache.pop(key, None)
        return None
    return rec["data"]

def _radar_cache_set(key, data):
    _radar_cache[key] = {"ts": time.time(), "data": data}

def headers_for():
    cfg = API_CFG["football"]
    return {"x-apisports-key": API_SPORTS_KEY, "x-rapidapi-host": cfg["host"]} if API_SPORTS_KEY else {}

def safe_get(url, headers, params=None, timeout=20):
    if not API_SPORTS_KEY:
        print("ERRO: A variável de ambiente API_SPORTS_KEY não está definida.")
        return None
    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"safe_get error para {url} com params {params}: {e}")
        return None

def _compute_sort_key(ev):
    elapsed = int(ev.get("time", {}).get("elapsed") or 0)
    extra = int(ev.get("time", {}).get("extra") or 0)
    return elapsed + extra

def _format_display_time(ev):
    t = ev.get("time", {}) or {}
    elapsed = t.get("elapsed")
    extra = t.get("extra")
    if elapsed is None:
        return "-"
    if extra:
        return f"{elapsed}+{extra}'"
    return f"{elapsed}'"

def classify_event(ev):
    detail = (ev.get("detail") or "").lower()
    if "goal" in detail: return "Gol"
    if "yellow card" in detail: return "Cartão Amarelo"
    if "red card" in detail: return "Cartão Vermelho"
    if "substitution" in detail: return "Substituição"
    if "corner" in detail: return "Escanteio"
    return ev.get("type", "Evento")

def try_int(v):
    try:
        if isinstance(v, str) and "%" in v:
            return int(v.replace("%", "").strip())
        return int(v)
    except (ValueError, TypeError, AttributeError):
        return 0

def stats_aovivo(game_id: int):
    ck = f"radar_stats_{game_id}_full"
    cached = _radar_cache_get(ck)
    if cached is not None:
        return cached
    try:
        base = API_CFG["football"]["base"]
        headers = headers_for()

        fixture_resp = safe_get(f"{base}/fixtures", headers, params={"id": game_id})
        if not fixture_resp or not fixture_resp.get("response"):
            print(f"ERRO: Fixture {game_id} não encontrado em radar_ia.")
            return None
        fixture = fixture_resp["response"][0]

        home_id = fixture.get("teams", {}).get("home", {}).get("id")

        stats_resp = safe_get(f"{base}/fixtures/statistics", headers, params={"fixture": game_id})
        full_stats = {"home": {}, "away": {}}
        if stats_resp and stats_resp.get("response"):
            for team_stats in stats_resp["response"]:
                tid = team_stats.get("team", {}).get("id")
                side = "home" if tid == home_id else "away"
                tmp = {}
                for s in (team_stats.get("statistics") or []):
                    k = (s.get("type") or "").strip().lower().replace(" ", "_")
                    tmp[k] = try_int(s.get("value"))
                full_stats[side].update(tmp)

        events_resp = safe_get(f"{base}/fixtures/events", headers, params={"fixture": game_id})
        events = events_resp.get("response", []) if events_resp else []
        processed = []
        for ev in events:
            processed.append({
                "display_time": _format_display_time(ev),
                "category": classify_event(ev),
                "detail": ev.get("detail"),
                "player": ev.get("player", {}).get("name"),
                "_sort": _compute_sort_key(ev)
            })
        processed.sort(key=lambda x: x["_sort"], reverse=True)

        result = {
            "fixture": fixture,
            "teams": fixture.get("teams", {}),
            "score": fixture.get("score", {}),
            "status": fixture.get("fixture", {}).get("status", {}),
            "statistics": full_stats,
            "events": processed,
        }
        _radar_cache_set(ck, result)
        return result
    except Exception:
        traceback.print_exc()
        return None

# =========================
# OPTA IA (análise de jogador)
# =========================
def get_players_for_team(team_id: int, season: int = datetime.now().year) -> Optional[List[Dict]]:
    if not API_SPORTS_KEY:
        print("ERRO: A variável de ambiente API_SPORTS_KEY não está definida.")
        return None
    try:
        params = {'team': team_id, 'season': season}
        r = requests.get(f"{API_URL_BASE}/players", headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        data = r.json().get('response', [])
        players = []
        for item in data:
            p = item.get('player', {}) or {}
            players.append({"id": p.get("id"), "name": p.get("name")})
        return players
    except Exception as e:
        print(f"ERRO ao buscar jogadores para o time {team_id}: {e}")
        return None

def process_and_analyze_stats(player_data: Dict) -> Dict:
    stats_list = player_data.get("statistics", []) or []
    aggregated = defaultdict(lambda: defaultdict(float))
    total_games = 0

    for entry in stats_list:
        games_block = entry.get("games", {}) or {}
        appearances = safe_int(games_block.get("appearences", 0))
        if appearances <= 0:
            continue
        total_games += appearances
        for category, block in entry.items():
            if isinstance(block, dict):
                for k, v in block.items():
                    try:
                        aggregated[category][k] += float(v or 0)
                    except Exception:
                        pass

    if total_games == 0:
        return {"key_stats": {}, "recommendations": []}

    def get_stat(cat, key):
        return aggregated.get(cat, {}).get(key, 0.0)

    key_stats = {
        "Gols (média/jogo)": f"{(get_stat('goals', 'total') / total_games):.2f}",
        "Chutes no Gol (m/jogo)": f"{(get_stat('shots', 'on') / total_games):.2f}",
    }
    recommendations = []
    avg_goals = get_stat('goals', 'total') / total_games
    if avg_goals > 0.35:
        recommendations.append({
            "market": "Jogador para Marcar",
            "recommendation": "Sim",
            "confidence": min(0.95, avg_goals / 0.7),
            "reason": f"Média de {avg_goals:.2f} gols por jogo."
        })
    return {"key_stats": key_stats, "recommendations": recommendations}

def analyze_player(player_id: int, season: int = datetime.now().year) -> Optional[Dict]:
    if not API_SPORTS_KEY:
        print("ERRO: A variável de ambiente API_SPORTS_KEY não está definida.")
        return None
    try:
        params = {'id': player_id, 'season': season}
        r = requests.get(f"{API_URL_BASE}/players", headers=HEADERS, params=params, timeout=20)
        r.raise_for_status()
        api_resp = r.json().get('response', [])
        if not api_resp:
            return None

        player_data = api_resp[0]
        player_info = {
            "name": player_data.get('player', {}).get('name'),
            "team": (player_data.get('statistics', [{}])[0].get('team', {}) or {}).get('name'),
        }
        analysis_result = process_and_analyze_stats(player_data)
        return {"player_info": player_info, **analysis_result}
    except requests.exceptions.RequestException as e:
        print(f"ERRO de API ao analisar jogador {player_id}: {e}")
        return None
    except Exception as e:
        print(f"ERRO interno ao analisar jogador {player_id}: {e}")
        return None

def analyze_player_stats(player_id: int, season: int = datetime.now().year):
    return analyze_player(player_id, season)

# =========================
# FORMATAÇÃO das saídas textuais
# =========================
def format_full_pre_game_analysis(game_analysis: dict, players_analysis: list) -> str:
    if not game_analysis or 'raw_fixture' not in game_analysis:
        return "Não foi possível obter a análise para este jogo."
    fixture = game_analysis.get('raw_fixture', {})
    home_team = fixture.get('teams', {}).get('home', {}).get('name', 'Casa')
    away_team = fixture.get('teams', {}).get('away', {}).get('name', 'Visitante')
    top3 = game_analysis.get('top3', [])
    lines = [f"📊 *Análise Completa — {home_team} vs {away_team}*"]
    lines.append("\n🤖 *TipsterIA — Análise da Partida*")
    if not top3:
        lines.append("_Nenhuma dica principal encontrada._")
    else:
        for pick in top3:
            line = f"- *{pick.get('market')}*: {pick.get('recommendation', 'N/A')} (conf: {pick.get('confidence')})"
            lines.append(line)
    lines.append("\n👤 *OptaIA — Jogadores em Destaque*")
    if not players_analysis:
        lines.append("_Nenhuma análise de jogador disponível._")
    else:
        for player_result in players_analysis:
            if player_result and player_result.get('player_info'):
                p_info = player_result['player_info']
                recs = player_result.get('recommendations', [])
                lines.append(f"\n*{p_info.get('name')}* ({p_info.get('team')})")
                if not recs:
                    lines.append("  - Sem dicas de aposta específicas.")
                else:
                    for rec in recs:
                        lines.append(f"  - *{rec.get('market')}*: {rec.get('recommendation')} (conf: {rec.get('confidence'):.2f})")
    lines.append("\n_Lembre-se: analise por conta própria._")
    return "\n".join(lines)

def format_live_analysis(radar_data: dict, live_tips: list) -> str:
    if not radar_data or 'fixture' not in radar_data:
        return "Não foi possível obter os dados ao vivo para este jogo."
    teams = radar_data.get('teams', {})
    status = radar_data.get('status', {})
    home_stats = radar_data.get('statistics', {}).get('home', {})
    home_team = teams.get('home', {}).get('name')
    away_team = teams.get('away', {}).get('name')
    score = radar_data.get('score', {}).get('fulltime', {})
    home_score = score.get('home', 0)
    away_score = score.get('away', 0)
    lines = [f"⚡ *Análise Ao Vivo — {home_team} {home_score} x {away_score} {away_team}*"]
    lines.append(f"\n📡 RadarIA — Status: {status.get('long')}")
    lines.append(f"\n📊 Estatísticas resumo (ex): Poss/Chutes/Escanteios")
    if not live_tips:
        lines.append("\n_Sem dicas ao vivo no momento._")
    else:
        lines.append("\n💡 *Dicas ao vivo:*")
        for tip in live_tips:
            lines.append(f"- {tip.get('market')}: {tip.get('recommendation')} (conf: {tip.get('confidence')}) — {tip.get('reason')}")
    return "\n".join(lines)

# =========================
# FLASK API
# =========================
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Betting IA Tipster ativo 🚀"})

# fixtures (API-FOOTBALL style) — original
@app.route("/fixtures", methods=["GET"])
def fixtures_endpoint():
    date_param = request.args.get("date")
    if date_param:
        raw = api_get_raw("fixtures", params={"date": date_param})
    else:
        today = datetime.utcnow().date().strftime("%Y-%m-%d")
        raw = api_get_raw("fixtures", params={"date": today})
    if not raw:
        return jsonify({"response": []}), 200
    return jsonify(raw), 200

# fixtures live (original)
@app.route("/fixtures/live", methods=["GET"])
def fixtures_live_raw():
    raw = api_get_raw("fixtures", params={"live": "all"})
    if not raw:
        return jsonify({"response": []}), 200
    return jsonify(raw), 200

# -----------------
# Compat endpoints tailored for index.js / bot
# -----------------
def _make_game_obj_from_fixture(f: dict) -> dict:
    fd = f.get("fixture", {}) or {}
    fid = fd.get("id")
    league = f.get("league", {}) or {}
    teams = f.get("teams", {}) or {}
    label = f"{teams.get('home',{}).get('name')} vs {teams.get('away',{}).get('name')}"
    return {
        "game_id": fid,
        "label": label,
        "league": {
            "id": league.get("id"),
            "country": league.get("country"),
            "name": league.get("name")
        },
        "raw": f
    }

@app.route("/pre-live-games", methods=["GET"])
def pre_live_games_compat():
    """
    Retorna lista de jogos pré-live (hoje) com campo 'league' (country + name).
    Ideal para o index.js agrupar por país/ligas.
    """
    fixtures = get_fixtures_for_dates(days_forward=0)
    out = []
    for f in fixtures:
        # only include scheduled (not live) when listing pre-live
        if f.get("type") == "scheduled":
            raw = f.get("raw") or {}
            out.append(_make_game_obj_from_fixture(raw))
    return jsonify(out), 200

@app.route("/live-games", methods=["GET"])
def live_games_compat():
    """
    Lista de jogos ao vivo simplificada (game_id, label, league).
    """
    raw = api_get_raw("fixtures", params={"live": "all"})
    out = []
    if raw and raw.get("response"):
        for f in raw["response"]:
            out.append(_make_game_obj_from_fixture(f))
    return jsonify(out), 200

# -----------------
# analyze endpoints (compat both styles)
# -----------------
@app.route("/analyze/game", methods=["POST"])
@app.route("/analyze-game", methods=["POST"])
def api_analyze_game():
    try:
        data = request.get_json() or {}
        game_id = data.get("game_id")
        if not game_id:
            return jsonify({"error": "game_id é obrigatório"}), 400
        game_analysis = analyze(int(game_id))
        players_analysis = []
        try:
            if game_analysis and game_analysis.get("raw_fixture"):
                fixture = game_analysis["raw_fixture"]
                home_id = fixture.get("teams", {}).get("home", {}).get("id")
                away_id = fixture.get("teams", {}).get("away", {}).get("id")
                if home_id:
                    players = get_players_for_team(home_id) or []
                    if players:
                        pid = players[0].get("id")
                        if pid:
                            p_analysis = analyze_player(pid)
                            if p_analysis:
                                players_analysis.append(p_analysis)
                if away_id:
                    players = get_players_for_team(away_id) or []
                    if players:
                        pid = players[0].get("id")
                        if pid:
                            p_analysis = analyze_player(pid)
                            if p_analysis:
                                players_analysis.append(p_analysis)
        except Exception:
            traceback.print_exc()
        text = format_full_pre_game_analysis(game_analysis or {}, players_analysis)
        return jsonify({"analysis_text": text, "raw": {"game_analysis": game_analysis, "players": players_analysis}}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/analyze/live", methods=["POST"])
@app.route("/analyze-live-game", methods=["POST"])
def api_analyze_live():
    try:
        data = request.get_json() or {}
        game_id = data.get("game_id")
        if not game_id:
            return jsonify({"error": "game_id é obrigatório"}), 400
        radar_data = stats_aovivo(int(game_id))
        live_tips = analyze_live_from_stats(radar_data or {})
        text = format_live_analysis(radar_data or {}, live_tips)
        return jsonify({"analysis_text": text, "raw": {"radar": radar_data, "tips": live_tips}}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Opta endpoints
@app.route("/players", methods=["GET"])
def api_players_old():
    try:
        player_id = request.args.get("id") or request.args.get("player_id")
        season = request.args.get("season") or datetime.now().year
        if not player_id:
            return jsonify({"error": "id do jogador é obrigatório (parâmetro id)"}), 400
        analysis = analyze_player(int(player_id), int(season))
        if analysis is None:
            return jsonify({"error": "Nenhum dado encontrado"}), 404
        return jsonify({"opta": analysis}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/opta-player", methods=["POST"])
def api_opta_player_post():
    try:
        data = request.get_json() or {}
        player_id = data.get("player_id")
        season = data.get("season") or datetime.now().year
        if not player_id:
            return jsonify({"error": "player_id é obrigatório"}), 400
        analysis = analyze_player(int(player_id), int(season))
        if analysis is None:
            return jsonify({"error": "Nenhum dado encontrado"}), 404
        return jsonify({"opta": analysis}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Rota legacy /pre-live-games também já implementada acima
# -----------------

# =========================
# RUN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Tipster API rodando na porta {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
