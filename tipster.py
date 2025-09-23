# tipster.py — UNIFICADO (sports_betting_analyzer + radar_ia + opta_ia + Flask API)
# Mantive toda a lógica de análise e helpers (cache, heurísticas, odds mapping, análise ao vivo, análise de jogadores).
import os
import time
import traceback
import requests
from datetime import datetime, date, timedelta
from collections import defaultdict, OrderedDict
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
# Pequenos helpers utilitários
# =========================
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

def format_conf_pct(confidence: Optional[float]) -> str:
    """Formata confiança float (0..1) para percent string (ex.: 0.45 -> '45%')."""
    try:
        c = float(confidence or 0.0)
        return f"{c*100:.0f}%"
    except Exception:
        return "0%"

# =========================
# SPORTS BETTING ANALYZER (pré-live)
# =========================

PREFERRED_BOOKMAKERS = ["bet365", "betano", "superbet", "pinnacle"]

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
    """
    Heurísticas simples: calcula 'power' por shots on goal e usa isso para sugerir moneyline.
    Se poucas previsões, preenche com picks genéricos de baixa confiança para garantir top3.
    """
    teams = fixture_raw.get("teams", {})
    home = teams.get("home", {}); away = teams.get("away", {})
    home_stats = stats_map.get(home.get("id"), {}); away_stats = stats_map.get(away.get("id"), {})
    def g(d, k): return d.get(k, 0)
    h_sot = g(home_stats, "Shots on Goal"); a_sot = g(away_stats, "Shots on Goal")
    # fallback: use shots_total if Shots on Goal not available
    if h_sot == 0:
        h_sot = g(home_stats, "Total shots") or g(home_stats, "Shots total") or g(home_stats, "Total shots on target")
    if a_sot == 0:
        a_sot = g(away_stats, "Total shots") or g(away_stats, "Shots total") or g(away_stats, "Total shots on target")
    h_power = h_sot * 1.6; a_power = a_sot * 1.6
    power_diff = h_power - a_power
    preds: List[dict] = []
    def add(market, rec, conf, reason=None): preds.append({"market": market, "recommendation": rec, "confidence": conf, "reason": reason or ""})
    # primary picks
    if power_diff > 4:
        add("moneyline", "Vitória Casa", 0.8, f"Diferença de força {round(power_diff,2)}")
    elif power_diff < -4:
        add("moneyline", "Vitória Visitante", 0.8, f"Diferença de força {round(power_diff,2)}")
    # additional heuristics
    # if many shots but few goals -> suggest Over 1.5
    total_shots = (h_sot or 0) + (a_sot or 0)
    if total_shots > 8:
        add("Total de Gols", "Mais de 1.5", 0.6, f"{total_shots} remates detectados")
    # both teams attacking
    if (h_sot or 0) > 2 and (a_sot or 0) > 2:
        add("Ambas Marcam", "Sim", 0.6, f"Remates: {h_sot} vs {a_sot}")
    summary = {"home_power": round(h_power, 2), "away_power": round(a_power, 2)}

    # Ensure at least 3 picks — fill with generic conservative picks if needed
    if len(preds) < 3:
        # pick 1: Over 1.5 or Under 2.5 depending on total_shots
        if total_shots >= 6:
            add("Total de Gols", "Mais de 1.5", 0.45, "Sugestão baseada em atividade ofensiva")
        else:
            add("Total de Gols", "Menos de 2.5", 0.45, "Sugestão conservadora por baixa atividade")
        # pick 2: Both Teams to Score conservative
        add("Ambas Marcam", "Sim", 0.40, "Sugestão conservadora")
    # If still <3, pad with neutral market
    while len(preds) < 3:
        add("Resultado Final", "Sem favorito definido", 0.30, "Dados limitados para definir favorito")

    return preds, summary

def enhance_predictions_with_preferred_odds(predictions: List[Dict], odds_raw: Optional[Dict]) -> List[Dict]:
    """
    Tenta mapear melhor odd / bookmaker preferido. Se não encontrar, deixa None.
    (Mantido simples — pois API de odds costuma variar por book).
    """
    if not odds_raw or not odds_raw.get("response"):
        return predictions
    # Simplistic: just attach placeholders if not present
    for pred in predictions:
        pred.setdefault("best_book", None)
        pred.setdefault("best_odd", None)
    return predictions

def analyze(game_id: int):
    """
    Análise pré-live principal. Sempre retorna um objeto com 'top3' com ao menos 3 sugestões.
    """
    fixture_data = api_get_raw("fixtures", params={"id": game_id})
    if not fixture_data or not fixture_data.get("response"):
        return None
    fixture = fixture_data["response"][0]
    stats_raw = fetch_football_statistics(game_id)
    stats_map = build_stats_map(stats_raw)
    preds, summary = heuristics_football(fixture, stats_map)
    odds_raw = api_get_raw("odds", params={"fixture": game_id})
    enhanced = enhance_predictions_with_preferred_odds(preds, odds_raw)
    # ensure top3 exists
    top3 = enhanced[:3]
    if len(top3) < 3:
        # pad with neutral picks if necessary
        while len(top3) < 3:
            top3.append({"market": "Resultado Final", "recommendation": "Sem favorito definido", "confidence": 0.30})
    return {
        "game_id": game_id,
        "summary": summary,
        "predictions": enhanced,
        "top3": top3,
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
    elapsed = status.get("elapsed", 0)   # ✅ corrigido aqui

    # ✅ Placar atualizado
    fixture = radar_data.get("fixture", {}) or {}
    score = fixture.get("goals", {}) or {}
    home_goals = score.get("home") or 0
    away_goals = score.get("away") or 0
    total_goals = home_goals + away_goals

    # normaliza stats (cada API usa chaves diferentes)
    def get_stat(side_stats, *keys):
        for k in keys:
            if k in side_stats:
                return side_stats.get(k) or 0
        return 0

    # ✅ separa remates totais e remates no gol
    home_shots_total = get_stat(home_stats, 'total_shots', 'shots_total', 'shots')
    away_shots_total = get_stat(away_stats, 'total_shots', 'shots_total', 'shots')
    total_shots = (home_shots_total or 0) + (away_shots_total or 0)

    home_shots_on = get_stat(home_stats, 'shots_on_target', 'shots_on', 'on_target', 'shots_on_goal')
    away_shots_on = get_stat(away_stats, 'shots_on_target', 'shots_on', 'on_target', 'shots_on_goal')

    home_corners = get_stat(home_stats, 'corner_kicks', 'corners', 'corner_kicks_full')
    away_corners = get_stat(away_stats, 'corner_kicks', 'corners', 'corner_kicks_full')
    total_corners = (home_corners or 0) + (away_corners or 0)

    def add_tip(market, recommendation, reason, confidence):
        tips.append({
            "market": market,
            "recommendation": recommendation,
            "reason": reason,
            "confidence": confidence
        })

    # =========================
    # 🔮 Estimativa de acréscimos baseada em eventos
    # =========================
    def estimate_extra_time(events: list, half: int = 1) -> int:
        total_seconds = 0
        for ev in events:
            minute = ev.get("time", {}).get("elapsed") or 0

            # filtra eventos por tempo
            if half == 1 and minute > 45:
                continue
            if half == 2 and minute <= 45:
                continue

            cat = ev.get("category", "").lower()
            if "falta" in cat:
                total_seconds += 15
            elif "amarelo" in cat:
                total_seconds += 30
            elif "gol" in cat:
                total_seconds += 60
            elif "var" in cat:
                start = ev.get("start_time")
                end = ev.get("end_time")
                if start and end:
                    total_seconds += max(int(end - start), 60)
                else:
                    total_seconds += 60
        return (total_seconds + 59) // 60

    # calcula acréscimo estimado e adiciona como dica
    if 35 <= elapsed < 45:
        extra_est = estimate_extra_time(radar_data.get("events", []), half=1)
        add_tip("Acréscimo 1º Tempo",
                f"Estimado: {extra_est} minutos",
                "Baseado nos eventos ocorridos no 1º tempo", 0.65)

    if 80 <= elapsed < 90:
        extra_est = estimate_extra_time(radar_data.get("events", []), half=2)
        add_tip("Acréscimo 2º Tempo",
                f"Estimado: {extra_est} minutos",
                "Baseado nos eventos ocorridos no 2º tempo", 0.65)

    # =========================
    # Sugestões baseadas em estatísticas
    # =========================
    if elapsed > 20:
        if total_shots > 7 and total_goals < 2:
            add_tip("Gols Asiáticos", f"Mais de {total_goals + 0.5}",
                    f"{total_shots} remates totais", 0.70)
        elif total_shots < 3:
            add_tip("Gols Asiáticos", f"Menos de {total_goals + 1.5}",
                    f"Apenas {total_shots} remates", 0.65)

    if (home_shots_total or 0) > 3 and (away_shots_total or 0) > 3 and total_goals < 3:
        add_tip("Ambas Marcam", "Sim",
                f"Ambas as equipas rematam ({home_shots_total} vs {away_shots_total})", 0.75)

    if elapsed > 25:
        if total_corners > 5:
            add_tip("Escanteios Asiáticos", f"Mais de {total_corners + 2}",
                    f"{total_corners} escanteios já cobrados", 0.80)
        elif total_shots > 10 and total_corners < 4:
            add_tip("Escanteios (Equipe)", "Próximo escanteio para o time mais ofensivo",
                    "Alta pressão e poucos cantos até agora", 0.60)

    if elapsed > 75:
        if total_goals == 0:
            add_tip("Total de Gols", "Menos de 1.5",
                    "Poucos golos e pouco tempo restante", 0.85)
        elif home_goals > away_goals:
            add_tip("Resultado Final", "Vitória do Time da Casa",
                    "Time da casa a segurar o resultado", 0.70)

    # fallback se não gerar nenhuma dica
    if not tips:
        if total_shots >= 6:
            add_tip("Total de Gols", "Mais de 1.5",
                    "Sugestão baseada na atividade de remates", 0.45)
        else:
            add_tip("Total de Gols", "Menos de 2.5",
                    "Sugestão conservadora devido a pouca atividade", 0.40)

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
# OPTA IA (análise de jogador) - expandida
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

def _sum_stat_if_exists(aggregated, category, key):
    try:
        return aggregated.get(category, {}).get(key, 0.0)
    except Exception:
        return 0.0

def process_and_analyze_stats(player_data: Dict) -> Dict:
    """
    Processa estatísticas do jogador e gera recomendações.
    Ampliado para capturar remates totais, remates ao golo, assistências, passes, 'rebotes' estimados e gerar recomendações para vários mercados.
    Garante pelo menos uma recomendação (mensagem clara) em caso de dados limitados.
    """
    stats_list = player_data.get("statistics", []) or []
    aggregated = defaultdict(lambda: defaultdict(float))
    total_games = 0

    # Normaliza e agrega vários campos vindos de diferentes formatos da API
    for entry in stats_list:
        games_block = entry.get("games", {}) or {}
        # API pode usar 'appearances' ou 'appearences' (typo) ou 'played'
        appearances = safe_int(games_block.get("appearences", 0) or games_block.get("appearances", 0) or games_block.get("played", 0))
        if appearances <= 0:
            continue
        total_games += appearances
        for category, block in entry.items():
            if isinstance(block, dict):
                for k, v in block.items():
                    # Some values might be nested or strings; try converter
                    try:
                        aggregated[category][k] += float(v or 0)
                    except Exception:
                        # se v for boolean/None ou outros, ignore
                        pass

    if total_games == 0:
        # Sem jogos/estatísticas significativas
        return {
            "key_stats": {},
            "recommendations": [
                {
                    "market": "Jogador para Marcar",
                    "recommendation": "Não",
                    "confidence": 0.15,
                    "reason": "Dados limitados: poucas ou nenhuma aparição registrada."
                }
            ]
        }

    # Função utilitária para pegar estatísticas de categorias diferentes de forma segura
    def get_stat(cat, key):
        return aggregated.get(cat, {}).get(key, 0.0)

    # Tentativas de normalizar vários nomes que a API pode usar
    goals_total = get_stat('goals', 'total') or get_stat('goals', 'goals') or 0.0
    assists_total = get_stat('goals', 'assists') or get_stat('goals', 'assists_total') or 0.0

    # Shots: pode aparecer em 'shots' ou 'shots' categoria com keys 'total'/'on'/'off'/'blocked'
    shots_total = get_stat('shots', 'total') or get_stat('shots', 'shots_total') or get_stat('shots', 'shots') or 0.0
    shots_on = get_stat('shots', 'on') or get_stat('shots', 'on_target') or get_stat('shots', 'shots_on') or 0.0
    shots_blocked = get_stat('shots', 'blocked') or get_stat('shots', 'blocks') or 0.0
    shots_off = shots_total - shots_on if shots_total and shots_on else get_stat('shots', 'off') or 0.0

    passes_total = get_stat('passes', 'total') or 0.0
    key_passes = get_stat('passes', 'key') or get_stat('passes', 'key_passes') or 0.0

    tackles_total = get_stat('tackles', 'total') or 0.0
    interceptions = get_stat('tackles', 'interceptions') or 0.0

    # Estimativa simples de 'rebotes' (se há estatística direta, usa; senão tenta inferir)
    rebounds_raw = get_stat('rebounds', 'total') or get_stat('shots', 'rebounds') or 0.0
    if not rebounds_raw:
        # inferência aproximada: bloqueados + remates fora (não é preciso, mas dá um proxy)
        rebounds_raw = shots_blocked or 0.0

    # médias por jogo
    avg_goals = goals_total / total_games
    avg_assists = assists_total / total_games
    avg_shots_total = shots_total / total_games
    avg_shots_on = shots_on / total_games
    avg_passes = passes_total / total_games
    avg_key_passes = key_passes / total_games
    avg_rebounds = rebounds_raw / total_games if total_games else 0.0

    key_stats = {
        "Jogos (aparições)": f"{int(total_games)}",
        "Gols (média/jogo)": f"{avg_goals:.2f}",
        "Assistências (média/jogo)": f"{avg_assists:.2f}",
        "Remates (média/jogo)": f"{avg_shots_total:.2f}",
        "Remates no Gol (média/jogo)": f"{avg_shots_on:.2f}",
        "Passes (média/jogo)": f"{avg_passes:.2f}",
        "Key Passes (média/jogo)": f"{avg_key_passes:.2f}",
        "Rebotes (estim.) (média/jogo)": f"{avg_rebounds:.2f}"
    }

    recommendations = []

    # Recomendação primária: Jogador para Marcar
    if avg_goals > 0.35:
        rec_conf = min(0.95, avg_goals / 0.7)  # escala a confiança
        recommendations.append({
            "market": "Jogador para Marcar",
            "recommendation": "Sim",
            "confidence": rec_conf,
            "reason": f"Média de {avg_goals:.2f} gol(s) por jogo nas últimas aparições."
        })
    else:
        # Se poucos golos mas muitos remates: sugerir mercado 'Chutes no Gol' ou 'Remates'
        if avg_shots_on >= 0.6:
            recommendations.append({
                "market": "Chutes no Gol",
                "recommendation": "Acima de 0.5",
                "confidence": min(0.60, avg_shots_on / 2),
                "reason": f"Média de {avg_shots_on:.2f} remates no golo por jogo."
            })
        elif avg_shots_total >= 1.2:
            recommendations.append({
                "market": "Remates (m/jogo)",
                "recommendation": f"Acima de {max(1, int(avg_shots_total))}",
                "confidence": min(0.55, avg_shots_total / 3),
                "reason": f"Média de {avg_shots_total:.2f} remates por jogo."
            })
        else:
            recommendations.append({
                "market": "Jogador para Marcar",
                "recommendation": "Não",
                "confidence": 0.18,
                "reason": "Média de golos baixa — sugestão conservadora."
            })

    # Assistências
    if avg_assists >= 0.25:
        recommendations.append({
            "market": "Assistências (jogador)",
            "recommendation": "Acima de 0.5",
            "confidence": min(0.65, avg_assists / 0.5),
            "reason": f"Média de {avg_assists:.2f} assistências por jogo."
        })

    # Passes / Key passes
    if avg_passes > 20:
        recommendations.append({
            "market": "Passes (m/jogo)",
            "recommendation": f"Acima de {int(max(20, (avg_passes//5)*5))}",
            "confidence": min(0.5, avg_passes / 100),
            "reason": f"Média de {avg_passes:.2f} passes por jogo."
        })
    if avg_key_passes >= 0.8:
        recommendations.append({
            "market": "Key Passes (jogador)",
            "recommendation": "Acima de 0.5",
            "confidence": min(0.5, avg_key_passes / 2),
            "reason": f"Média de {avg_key_passes:.2f} key passes por jogo."
        })

    # Rebounds / remates bloqueados
    if avg_rebounds >= 0.3:
        recommendations.append({
            "market": "Rebotes/Remates bloqueados (estim.)",
            "recommendation": "Acima de 0.5",
            "confidence": min(0.45, avg_rebounds / 1),
            "reason": f"Estimativa de {avg_rebounds:.2f} por jogo (base em remates bloqueados/estatísticas disponíveis)."
        })

    # Guarantee at least one recommendation (shouldn't be empty)
    if not recommendations:
        recommendations.append({
            "market": "Jogador para Marcar",
            "recommendation": "Não",
            "confidence": 0.15,
            "reason": "Dados limitados: nenhuma métrica suficientemente forte para recomendação positiva."
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
        # ensure there is at least one recommendation
        recs = analysis_result.get("recommendations") or []
        if not recs:
            recs = [{
                "market": "Jogador para Marcar",
                "recommendation": "Não",
                "confidence": 0.15,
                "reason": "Dados limitados — fallback conservador."
            }]
            analysis_result["recommendations"] = recs
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
# FORMATAÇÃO das saídas textuais (WhatsApp-friendly)
# =========================
def format_player_analysis(player_analysis: dict) -> str:
    if not player_analysis or not player_analysis.get("player_info"):
        return "❌ Não foi possível obter análise para este jogador."

    p_info = player_analysis["player_info"]
    lines = [f"👤 *{p_info.get('name')}* ({p_info.get('team')})"]

    key_stats = player_analysis.get("key_stats", {})
    if key_stats:
        lines.append("\n📊 *Estatísticas principais*:")
        for k, v in key_stats.items():
            lines.append(f"- {k}: {v}")

    recs = player_analysis.get("recommendations", [])
    if recs:
        lines.append("\n💡 *Recomendações:*")
        for r in recs:
            conf_txt = format_conf_pct(r.get("confidence"))
            lines.append(
                f"- {r['market']}: {r['recommendation']} (conf: {conf_txt}) — {r['reason']}"
            )
    else:
        lines.append("\n_Sem recomendações disponíveis._")

    return "\n".join(lines)


def format_full_pre_game_analysis(game_analysis: dict, players_analysis: list) -> str:
    if not game_analysis or "raw_fixture" not in game_analysis:
        return "Não foi possível obter a análise para este jogo."

    fixture = game_analysis.get("raw_fixture", {})
    home_team = fixture.get("teams", {}).get("home", {}).get("name", "Casa")
    away_team = fixture.get("teams", {}).get("away", {}).get("name", "Visitante")
    top3 = game_analysis.get("top3", [])

    lines = [f"📊 *Análise Completa — {home_team} vs {away_team}*"]
    lines.append("\n🤖 *TipsterIA — Análise da Partida*")

    if not top3:
        lines.append("_Nenhuma dica principal encontrada._")
    else:
        for pick in top3:
            conf_txt = format_conf_pct(pick.get("confidence"))
            line = f"- *{pick.get('market')}*: {pick.get('recommendation', 'N/A')} (conf: {conf_txt})"
            if pick.get("reason"):
                line += f" — {pick.get('reason')}"
            if pick.get("best_book"):
                line += f" [{pick.get('best_book')} {pick.get('best_odd')}]"
            lines.append(line)

    lines.append("\n👤 *OptaIA — Jogadores em Destaque*")
    if not players_analysis:
        lines.append("_Nenhuma análise de jogador disponível._")
    else:
        for player_result in players_analysis:
            if player_result and player_result.get("player_info"):
                p_info = player_result["player_info"]
                recs = player_result.get("recommendations", [])
                key_stats = player_result.get("key_stats", {})
                lines.append(f"\n*{p_info.get('name')}* ({p_info.get('team')})")
                if key_stats:
                    ks_line = "  • " + " | ".join(
                        [f"{k}: {v}" for k, v in key_stats.items() if v is not None]
                    )
                    lines.append(ks_line)
                if not recs:
                    lines.append("  - Sem dicas de aposta específicas.")
                else:
                    for rec in recs:
                        conf_txt = format_conf_pct(rec.get("confidence"))
                        lines.append(
                            f"  - *{rec.get('market')}*: {rec.get('recommendation')} (conf: {conf_txt}) — {rec.get('reason','')}"
                        )

    lines.append(
        "\n_Lembre-se: analise por conta própria — estas são sugestões automáticas._"
    )
    return "\n".join(lines)


def format_live_analysis(radar_data: dict, live_tips: list) -> str:
    if not radar_data or "fixture" not in radar_data:
        return "Não foi possível obter os dados ao vivo para este jogo."

    teams = radar_data.get("teams", {})
    status = radar_data.get("status", {})
    home_team = teams.get("home", {}).get("name")
    away_team = teams.get("away", {}).get("name")

    # ✅ placar atualizado (sem depender só de fulltime)
    fixture = radar_data.get("fixture", {}) or {}
    score = fixture.get("goals", {}) or {}
    home_goals = score.get("home") or 0
    away_goals = score.get("away") or 0

    # fallback se ainda vier zerado
    if home_goals == 0 and away_goals == 0:
        fixture_score = fixture.get("score", {})
        home_goals = fixture_score.get("home", home_goals)
        away_goals = fixture_score.get("away", away_goals)

    lines = [
        f"⚡ *Análise Ao Vivo — {home_team} {home_goals} x {away_goals} {away_team}*"
    ]
    lines.append(f"\n📡 RadarIA — Status: {status.get('long')}")


    # ✅ Estimativa de acréscimos (baseada em eventos)
    def estimate_extra_time(events: list) -> int:
        total_seconds = 0
        for ev in events:
            cat = ev.get("category", "").lower()
            if "falta" in cat:
                total_seconds += 15
            elif "amarelo" in cat:
                total_seconds += 30
            elif "gol" in cat:
                total_seconds += 60
            elif "var" in cat:
                start = ev.get("start_time")
                end = ev.get("end_time")
                if start and end:
                    total_seconds += max(int(end - start), 60)
                else:
                    total_seconds += 60
        return (total_seconds + 59) // 60

    elapsed = status.get("elapsed", 0)
    if 35 <= elapsed < 45 or 80 <= elapsed < 90:
        extra_est = estimate_extra_time(radar_data.get("events", []))
        lines.append(f"⏱️ Estimativa de Acréscimo: {extra_est} minutos")

    # ✅ helper para normalizar estatísticas
    def get_stat(side_stats, *keys):
        for k in keys:
            if k in side_stats:
                return side_stats.get(k) or 0
        return 0

    # ✅ Estatísticas do Radar
    stats = radar_data.get("statistics", {})
    if stats:
        home_stats = stats.get("home", {})
        away_stats = stats.get("away", {})

        lines.append("\n📊 Estatísticas principais:")
        lines.append(f"- Remates: {get_stat(home_stats,'total_shots','shots_total','shots')} x "
                     f"{get_stat(away_stats,'total_shots','shots_total','shots')}")
        lines.append(f"- Remates no Gol: {get_stat(home_stats,'shots_on_target','shots_on','on_target','shots_on_goal')} x "
                     f"{get_stat(away_stats,'shots_on_target','shots_on','on_target','shots_on_goal')}")
        lines.append(f"- Escanteios: {get_stat(home_stats,'corner_kicks','corners','corner','corner_kicks_full')} x "
                     f"{get_stat(away_stats,'corner_kicks','corners','corner','corner_kicks_full')}")
        lines.append(f"- Cartões Amarelos: {get_stat(home_stats,'yellow_cards')} x {get_stat(away_stats,'yellow_cards')}")
        lines.append(f"- Cartões Vermelhos: {get_stat(home_stats,'red_cards')} x {get_stat(away_stats,'red_cards')}")
        lines.append(f"- Posse de Bola: {get_stat(home_stats,'ball_possession')}% x {get_stat(away_stats,'ball_possession')}%")
    else:
        lines.append("\n📊 Estatísticas não disponíveis no momento.")

    # ✅ Dicas ao vivo
    if not live_tips:
        lines.append("\n_Sem dicas ao vivo no momento._")
    else:
        lines.append("\n💡 *Dicas ao vivo:*")
        for tip in live_tips:
            conf_txt = format_conf_pct(tip.get("confidence"))
            lines.append(
                f"- {tip.get('market')}: {tip.get('recommendation')} (conf: {conf_txt}) — {tip.get('reason')}"
            )

    # ✅ Eventos recentes
    events = radar_data.get("events", [])
    if events:
        lines.append("\n📌 Eventos recentes:")
        for ev in events[:5]:
            lines.append(f"- {ev['display_time']} {ev['category']} ({ev.get('player') or ''})")

    # ✅ Tempo com acréscimo oficial (se a API já mandar)
    extra = status.get("extra")
    if elapsed:
        if extra:
            lines.append(f"\n⏱️ Tempo Oficial: {elapsed}+{extra}'")
        else:
            lines.append(f"\n⏱️ Tempo: {elapsed}'")

    lines.append("\n_Lembre-se: apostas ao vivo são voláteis — jogue com responsabilidade._")
    return "\n".join(lines)

# =========================
# FLASK API
# =========================
app = Flask(__name__)

def format_menu_text():
    return (
        "📊 *Betting IA*\n\n"
        "1️⃣ Ver jogos disponíveis (Pré)\n"
        "2️⃣ Analisar Jogos Pré\n"
        "3️⃣ Analisar Jogos ao Vivo\n"
        "4️⃣ Radar Futebol (Estatísticas ao vivo)\n"
        "5️⃣ Estatísticas de jogador (Opta)\n\n"
        "Digite o número correspondente ao jogo que deseja a análise.\n"
        "Digite 0️⃣ para voltar ao menu principal a qualquer momento."
    )

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Betting IA Tipster ativo 🚀", "menu": format_menu_text()})

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
# Helpers para agrupar por País -> Liga -> Jogos
# -----------------
def _make_game_obj_from_fixture(f: dict) -> dict:
    fd = f.get("fixture", {}) or {}
    fid = fd.get("id")
    league = f.get("league", {}) or {}
    teams = f.get("teams", {}) or {}
    label = f"{teams.get('home',{}).get('name')} vs {teams.get('away',{}).get('name')}"
    # We keep game_id (server-side), but when presenting to the client
    # you can use 'label' only (hide id) — client may still need id for analysis endpoints.
    return {
        "game_id": fid,
        "label": label,
        "utc_date": fd.get("date"),
        "league": {
            "id": league.get("id"),
            "country": league.get("country"),
            "name": league.get("name")
        },
        "raw": f
    }

def group_fixtures_by_country_league(fixtures: List[dict]) -> List[Dict[str, Any]]:
    """
    Recebe lista de fixtures (raw from API) e retorna lista ordenada:
    [
        {
            "country": "Mexico",
            "leagues": [
                {
                    "league_id": 123,
                    "league_name": "Liga MX",
                    "games": [ {game obj}, ... ]
                }, ...
            ]
        }, ...
    ]
    """
    buckets: Dict[str, Dict[str, Any]] = OrderedDict()
    for f in fixtures:
        league = f.get("league", {}) or {}
        country = league.get("country") or "Unknown"
        league_name = league.get("name") or "Unknown League"
        league_id = league.get("id")
        if country not in buckets:
            buckets[country] = {"country": country, "leagues": OrderedDict()}
        leagues_map = buckets[country]["leagues"]
        if league_id not in leagues_map:
            leagues_map[league_id] = {"league_id": league_id, "league_name": league_name, "games": []}
        leagues_map[league_id]["games"].append(_make_game_obj_from_fixture(f))
    # convert OrderedDicts to lists
    out = []
    for country, cdata in buckets.items():
        leagues_list = []
        for lid, ldata in cdata["leagues"].items():
            leagues_list.append(ldata)
        out.append({"country": country, "leagues": leagues_list})
    return out

# -----------------
# Compat endpoints tailored for index.js / bot
# -----------------
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

@app.route("/pre-live-games/full", methods=["GET"])
def pre_live_games_full():
    # mesma coisa mas devolve raw API-Football response style
    fixtures = get_fixtures_for_dates(days_forward=0)
    out = []
    for f in fixtures:
        if f.get("type") == "scheduled":
            out.append(f.get("raw"))
    return jsonify({"response": out}), 200

@app.route("/pre-live-grouped", methods=["GET"])
def pre_live_grouped():
    """
    Retorna fixtures pré-live agrupados por country -> leagues -> games
    (útil para exibir no bot por país/league sem expor IDs diretamente).
    """
    fixtures = get_fixtures_for_dates(days_forward=0)
    raws = [f.get("raw") for f in fixtures if f.get("type") == "scheduled"]
    if not raws:
        return jsonify([]), 200
    grouped = group_fixtures_by_country_league(raws)
    return jsonify(grouped), 200

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

@app.route("/live-games/full", methods=["GET"])
def live_games_full():
    raw = api_get_raw("fixtures", params={"live": "all"})
    return jsonify(raw or {"response": []}), 200

@app.route("/live-games-grouped", methods=["GET"])
def live_games_grouped():
    raw = api_get_raw("fixtures", params={"live": "all"})
    if not raw or not raw.get("response"):
        return jsonify([]), 200
    grouped = group_fixtures_by_country_league(raw["response"])
    return jsonify(grouped), 200

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
                        # try to analyze a couple of most likely scorers (first two players)
                        pid = players[0].get("id")
                        if pid:
                            p_analysis = analyze_player(pid)
                            if p_analysis:
                                players_analysis.append(p_analysis)
                        if len(players) > 1:
                            pid2 = players[1].get("id")
                            if pid2:
                                p2_analysis = analyze_player(pid2)
                                if p2_analysis:
                                    players_analysis.append(p2_analysis)
                if away_id and len(players_analysis) < 2:
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

# radar-only endpoint (for explicit radar menu)
@app.route("/analyze/radar", methods=["POST"])
def api_analyze_radar():
    try:
        data = request.get_json() or {}
        game_id = data.get("game_id")
        if not game_id:
            return jsonify({"error": "game_id é obrigatório"}), 400

        radar_data = stats_aovivo(int(game_id))
        if not radar_data:
            return jsonify({"analysis_text": "❌ Não foi possível obter dados do radar."}), 200

        # ✅ Reaproveita o mesmo formatador usado no live
        live_tips = analyze_live_from_stats(radar_data or {})
        text = format_live_analysis(radar_data, live_tips)

        return jsonify({
            "analysis_text": text,
            "raw": {
                "radar": radar_data,
                "tips": live_tips
            }
        }), 200

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
        analysis_text = format_player_analysis(analysis)
        return jsonify(
            {"opta": {**analysis, "analysis_text": analysis_text}}
        ), 200
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
        analysis_text = format_player_analysis(analysis)
        return jsonify(
            {"opta": {**analysis, "analysis_text": analysis_text}}
        ), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# -----------------
# Novos endpoints utilitários para o menu Opta / front-end
# -----------------
@app.route("/opta/leagues", methods=["GET"])
def opta_leagues():
    """
    Retorna lista simples de ligas extraídas dos fixtures (para popular dropdown).
    """
    try:
        # pegar fixtures próximos 2 dias (hoje, amanhã)
        fixtures = get_fixtures_for_dates(days_forward=2)
        leagues_map = {}
        for f in fixtures:
            raw = f.get("raw") or {}
            league = raw.get("league", {}) or {}
            lid = league.get("id")
            if not lid:
                continue
            leagues_map[lid] = {"id": lid, "name": league.get("name"), "country": league.get("country")}
        out = list(leagues_map.values())
        return jsonify(out), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify([]), 200

@app.route("/opta/teams", methods=["GET"])
def opta_teams():
    """
    /opta/teams?league_id=123
    Retorna times para a liga informada (baseado nos fixtures próximos dias).
    """
    try:
        league_id = request.args.get("league_id")
        if not league_id:
            return jsonify({"error": "league_id obrigatório"}), 400
        fixtures = get_fixtures_for_dates(days_forward=2)
        teams_map = {}
        for f in fixtures:
            raw = f.get("raw") or {}
            league = raw.get("league", {}) or {}
            if str(league.get("id")) != str(league_id):
                continue
            teams = raw.get("teams", {}) or {}
            home = teams.get("home", {}) or {}
            away = teams.get("away", {}) or {}
            if home.get("id"):
                teams_map[home.get("id")] = {"id": home.get("id"), "name": home.get("name")}
            if away.get("id"):
                teams_map[away.get("id")] = {"id": away.get("id"), "name": away.get("name")}
        out = list(teams_map.values())
        return jsonify(out), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify([]), 200

@app.route("/opta/players", methods=["GET"])
def opta_players():
    """
    /opta/players?team_id=123
    Retorna jogadores (id,name) para o time via API-FOOTBALL players endpoint.
    """
    try:
        team_id = request.args.get("team_id")
        season = request.args.get("season") or datetime.now().year
        if not team_id:
            return jsonify({"error": "team_id obrigatório"}), 400
        players = get_players_for_team(int(team_id), int(season)) or []
        return jsonify(players), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify([]), 200

# Rota legacy /pre-live-games também já implementada acima
# -----------------

# =========================
# RUN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Tipster API rodando na porta {port} (modo local)")
    app.run(host="0.0.0.0", port=port, debug=True)
