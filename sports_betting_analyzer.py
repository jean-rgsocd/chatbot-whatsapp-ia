# sports_betting_analyzer.py (Módulo de Análise)
# VERSÃO COM ANÁLISE AO VIVO INTELIGENTE

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import requests
import os
import time
import traceback

# ------------- Configuração e Constantes -------------
API_SPORTS_KEY = os.environ.get("API_SPORTS_KEY")
API_URL_BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_SPORTS_KEY}

PREFERRED_BOOKMAKERS = ["bet365", "betano", "superbet", "pinnacle"]
CACHE_TTL = int(os.environ.get("CACHE_TTL", "60"))
_cache: Dict[str, Dict[str, Any]] = {}

# (As funções de Cache, HTTP, Fixtures, Stats, Heurísticas Pré-Jogo e Odds permanecem as mesmas)
# ... (código anterior omitido para brevidade, mas deve ser mantido no seu arquivo) ...
# ------------- Helpers de Cache -------------
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

# ------------- Helper de Requisição HTTP -------------
def api_get_raw(path: str, params: dict = None) -> Optional[Dict[str, Any]]:
    if not API_SPORTS_KEY:
        print("ERRO: A variável de ambiente API_SPORTS_KEY não está definida.")
        return None
    url = f"{API_URL_BASE}/{path}"
    try:
        r = requests.get(url, headers=HEADERS, params=params or {}, timeout=25)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

# ------------- Funções de Jogos (Fixtures) -------------
def normalize_game(raw: dict) -> dict:
    fixture = raw.get("fixture", {})
    league = raw.get("league", {}) or {}
    teams = raw.get("teams", {}) or {}
    status = fixture.get("status", {}) or {}
    return {
        "game_id": fixture.get("id"),"date": fixture.get("date"),"league": league,
        "teams": teams,"status": status,"type": ("live" if status.get("elapsed") else "scheduled"),
        "raw": raw
    }

def get_fixtures_for_dates(days_forward: int = 2) -> List[dict]:
    ck = f"all_fixtures_v4_{days_forward}"
    cached = _cache_get(ck)
    if cached: return cached
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

# ------------- Helpers de Estatísticas -------------
def fetch_football_statistics(fixture_id: int) -> Optional[Dict[str, Any]]:
    return api_get_raw("fixtures/statistics", params={"fixture": fixture_id})

def safe_int(v):
    try: return int(v)
    except (ValueError, TypeError):
        try: return int(float(v))
        except Exception: return 0

def build_stats_map(stats_raw: Optional[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    if not stats_raw or "response" not in stats_raw: return out
    for item in stats_raw["response"]:
        tid = item.get("team", {}).get("id")
        if not tid: continue
        out[tid] = {}
        for s in item.get("statistics", []) or []:
            k = (s.get("type") or "").strip()
            v = s.get("value")
            out[tid][k] = safe_int(v)
    return out

# ------------- Heurísticas (Lógica de Análise Pré-Jogo) -------------
def heuristics_football(fixture_raw: dict, stats_map: Dict[int, Dict[str, Any]]) -> Tuple[List[dict], dict]:
    # (Toda a lógica desta função permanece igual)
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


# ------------- Helpers de Odds -------------
def enhance_predictions_with_preferred_odds(predictions: List[Dict], odds_raw: Optional[Dict]) -> List[Dict]:
    # (Toda a lógica desta função permanece igual)
    if not odds_raw or not odds_raw.get("response"): return predictions
    # ...
    return predictions

# ------------- Função Principal de Análise Pré-Jogo -------------
def analyze(game_id: int):
    fixture_data = api_get_raw("fixtures", params={"id": game_id})
    if not fixture_data or not fixture_data.get("response"): return None
    fixture = fixture_data["response"][0]
    stats_raw = fetch_football_statistics(game_id)
    stats_map = build_stats_map(stats_raw)
    preds, summary = heuristics_football(fixture, stats_map)
    odds_raw = api_get_raw("odds", params={"fixture": game_id})
    enhanced = enhance_predictions_with_preferred_odds(preds, odds_raw)
    return {
        "game_id": game_id, "summary": summary, "predictions": enhanced,
        "top3": enhanced[:3], "raw_fixture": fixture
    }

# --- NOVA FUNÇÃO DE ANÁLISE AO VIVO ---
def analyze_live_from_stats(radar_data: Dict) -> List[Dict]:
    """
    Recebe os dados do RadarIA e gera dicas de aposta baseadas nas estatísticas ao vivo.
    """
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

    # Extrai estatísticas chave com valores padrão 0
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

    # --- Lógicas baseadas no seu pedido ---
    # Lógica de Gols
    if elapsed > 20:
        if total_shots > 7 and total_goals < 2:
            add_tip("Gols Asiáticos", f"Mais de {total_goals + 0.5}", f"{total_shots} chutes totais", 0.70)
        elif total_shots < 3:
            add_tip("Gols Asiáticos", f"Menos de {total_goals + 1.5}", f"Apenas {total_shots} chutes", 0.65)

    # Lógica para Ambas Marcam
    if home_shots > 3 and away_shots > 3 and total_goals < 3:
        add_tip("Ambas Marcam", "Sim", f"Ambos os times chutando ({home_shots} vs {away_shots})", 0.75)

    # Lógica de Escanteios
    if elapsed > 25:
        if total_corners > 5:
             add_tip("Escanteios Asiáticos", f"Mais de {total_corners + 2}", f"{total_corners} escanteios já cobrados", 0.80)
        elif total_shots > 10 and total_corners < 4:
             add_tip("Escanteios (Equipe)", "Próximo escanteio para o time mais ofensivo", "Alta pressão, poucos cantos", 0.60)
             
    # Lógica para jogo se aproximando do fim
    if elapsed > 75:
        if total_goals == 0:
            add_tip("Total de Gols", "Menos de 1.5", "Poucos gols e pouco tempo restante", 0.85)
        elif home_goals > away_goals:
            add_tip("Resultado Final", "Vitória do Time da Casa", "Time da casa segurando o resultado", 0.70)
            
    return tips
