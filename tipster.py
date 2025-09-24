# tipster.py — VERSÃO FINAL, COMPLETA E CORRIGIDA
# Baseado no seu código de 1447 linhas, com duplicatas removidas e melhorias aplicadas.
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
    print("WARNING: API_SPORTS_KEY não definida — muitas rotas vão falhar sem a chave.")

API_URL_BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_SPORTS_KEY} if API_SPORTS_KEY else {}
PREFERRED_BOOKMAKERS = [b.lower() for b in (os.environ.get("PREFERRED_BOOKMAKERS", "bet365,betano,superbet,pinnacle").split(","))]


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

def get_fixtures_for_dates(days_forward: int = 2) -> List[dict]:
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

# ALTERADO: Heurísticas pré-live com mais variedade de mercados
def heuristics_football(fixture_raw: dict, stats_map: Dict[int, Dict[str, Any]]) -> Tuple[List[dict], dict]:
    teams = fixture_raw.get("teams", {})
    home, away = teams.get("home", {}), teams.get("away", {})
    home_stats, away_stats = stats_map.get(home.get("id"), {}), stats_map.get(away.get("id"), {})
    
    def g(d, k_list):
        for k in k_list:
            if val := d.get(k): return val
        return 0

    h_sot = g(home_stats, ["Shots on Goal", "Total shots"])
    a_sot = g(away_stats, ["Shots on Goal", "Total shots"])
    h_form = g(home_stats, ["Form"])
    a_form = g(away_stats, ["Form"])
    
    h_power = h_sot * 1.5 + (h_form.count('W') * 2 if isinstance(h_form, str) else 0)
    a_power = a_sot * 1.5 + (a_form.count('W') * 2 if isinstance(a_form, str) else 0)
    power_diff = h_power - a_power
    total_shots = h_sot + a_sot
    
    preds: List[dict] = []
    def add(market, rec, conf, reason): preds.append({"market": market, "recommendation": rec, "confidence": conf, "reason": reason})

    if power_diff > 5:
        add("Resultado Final", "Vitória Casa", 0.75, f"Casa com maior poder de fogo ({power_diff:.1f})")
        add("Handicap Asiático", "Casa -0.5", 0.65, "Casa é favorita para vencer")
    elif power_diff < -5:
        add("Resultado Final", "Vitória Visitante", 0.75, f"Visitante com maior poder de fogo ({power_diff:.1f})")
        add("Handicap Asiático", "Visitante -0.5", 0.65, "Visitante é favorito para vencer")
    else:
        add("Dupla Chance", "Casa ou Empate", 0.50, "Jogo equilibrado, casa tem pequena vantagem")

    if total_shots > 10:
        add("Total de Gols", "Mais de 1.5", 0.65, f"{total_shots} remates esperados no total")
    elif total_shots < 5:
        add("Total de Gols", "Menos de 2.5", 0.60, "Equipes com baixa média de remates")

    if h_sot > 4 and a_sot > 4:
        add("Ambas Marcam", "Sim", 0.70, f"Ambas as equipes criam chances ({h_sot} vs {a_sot} remates)")

    preds.sort(key=lambda x: x['confidence'], reverse=True)
    summary = {"home_power": round(h_power, 2), "away_power": round(a_power, 2)}
    
    if len(preds) < 3:
        if not any(p['market'] == 'Total de Gols' for p in preds): add("Total de Gols", "Mais de 1.5", 0.45, "Sugestão conservadora")
        if not any(p['market'] == 'Ambas Marcam' for p in preds): add("Ambas Marcam", "Sim", 0.40, "Sugestão conservadora")
        if not any(p['market'] == 'Resultado Final' for p in preds): add("Resultado Final", "Sem favorito definido", 0.30, "Dados limitados")
    
    return preds[:3], summary

# NOVO/ALTERADO: Função de odds refeita para ser mais robusta e substituir a antiga
def enhance_predictions_with_odds(predictions: List[Dict], fixture_id: int) -> List[Dict]:
    for pred in predictions:
        pred.setdefault("best_odd", None)
        pred.setdefault("best_book", None)

    odds_raw = api_get_raw("odds", params={"fixture": str(fixture_id)})
    if not odds_raw or not odds_raw.get("response"):
        return predictions

    best_odds_map = {}
    try:
        for bookmaker_data in odds_raw["response"]:
            bookmaker_name = bookmaker_data.get("bookmaker", {}).get("name", "").lower()
            for market in bookmaker_data.get("bets", []):
                market_name = market.get("name", "")
                for value in market.get("values", []):
                    key = f"{market_name}:{value.get('value')}"
                    odd = safe_float(value.get('odd'))
                    is_preferred = 1 if bookmaker_name in PREFERRED_BOOKMAKERS else 0
                    if key not in best_odds_map or odd > best_odds_map[key]['odd'] or (odd == best_odds_map[key]['odd'] and is_preferred > best_odds_map[key]['is_preferred']):
                        best_odds_map[key] = {'odd': odd, 'bookmaker': bookmaker_name.capitalize(), 'is_preferred': is_preferred}
    except Exception as e:
        print(f"Erro ao processar odds para fixture {fixture_id}: {e}")
        return predictions

    market_map = {
        "Resultado Final:Vitória Casa": "Match Winner:Home", "Resultado Final:Vitória Visitante": "Match Winner:Away", "Resultado Final:Empate": "Match Winner:Draw",
        "Dupla Chance:Casa ou Empate": "Double Chance:Home/Draw", "Dupla Chance:Fora ou Empate": "Double Chance:Away/Draw",
        "Ambas Marcam:Sim": "Both Teams to Score:Yes", "Ambas Marcam:Não": "Both Teams to Score:No",
        "Total de Gols:Mais de": "Over/Under:Over", "Total de Gols:Menos de": "Over/Under:Under",
        "Escanteios Asiáticos:Mais de": "Asian Corners:Over", "Escanteios Asiáticos:Menos de": "Asian Corners:Under",
        "Handicap Asiático:Casa": "Asian Handicap:Home", "Handicap Asiático:Visitante": "Asian Handicap:Away"
    }

    for pred in predictions:
        rec = pred['recommendation']
        market_base = pred['market']
        found_odd = None
        
        if any(keyword in rec for keyword in ["Mais de", "Menos de", "-0.5", "+0.5"]):
            parts = rec.split(" ")
            value = parts[-1]
            key_part_base = parts[0] if market_base == "Handicap Asiático" else " ".join(parts[:-1])
            key_part = f"{market_base}:{key_part_base}"
            if api_market_base := market_map.get(key_part):
                found_odd = best_odds_map.get(f"{api_market_base} {value}")
        else:
            key = f"{market_base}:{rec}"
            if api_key := market_map.get(key):
                found_odd = best_odds_map.get(api_key)
        
        if found_odd:
            pred['best_odd'] = found_odd['odd']
            pred['best_book'] = found_odd['bookmaker']
            
    return predictions

def analyze(game_id: int):
    fixture_data = api_get_raw("fixtures", params={"id": game_id})
    if not fixture_data or not fixture_data.get("response"):
        return None
    fixture = fixture_data["response"][0]
    stats_raw = fetch_football_statistics(game_id)
    stats_map = build_stats_map(stats_raw)
    preds, summary = heuristics_football(fixture, stats_map)
    enhanced = enhance_predictions_with_odds(preds, game_id) # CORRIGIDO: usa a nova função de odds
    
    top3 = enhanced[:3]
    if len(top3) < 3:
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
# ANÁLISE AO VIVO E RADAR
# =========================
# ALTERADO: Lógica de análise ao vivo completamente refeita para incluir mais mercados
def analyze_live_from_stats(radar_data: Dict) -> List[Dict]:
    if not radar_data: return []

    tips = []
    stats = radar_data.get("statistics", {})
    home_stats, away_stats = stats.get("home", {}), stats.get("away", {})
    status = radar_data.get("status", {})
    elapsed = status.get("elapsed", 0)
    
    score = radar_data.get("goals", {})
    home_goals, away_goals = score.get("home", 0), score.get("away", 0)
    total_goals = home_goals + away_goals

    def get_stat(side_stats, *keys):
        for k in keys:
            if k in side_stats: return side_stats.get(k, 0)
        return 0

    home_shots_total = get_stat(home_stats, 'total_shots', 'shots_total')
    away_shots_total = get_stat(away_stats, 'total_shots', 'shots_total')
    total_shots = home_shots_total + away_shots_total
    home_shots_on = get_stat(home_stats, 'shots_on_goal', 'shots_on_target')
    away_shots_on = get_stat(away_stats, 'shots_on_goal', 'shots_on_target')
    home_corners = get_stat(home_stats, 'corner_kicks', 'corners')
    away_corners = get_stat(away_stats, 'corner_kicks', 'corners')
    total_corners = home_corners + away_corners

    def add_tip(market, rec, reason, conf):
        tips.append({"market": market, "recommendation": rec, "reason": reason, "confidence": conf})

    # Lógica de Gols
    if 25 < elapsed < 80 and total_shots > (elapsed / 7) and total_goals < 3:
        add_tip("Total de Gols", f"Mais de {total_goals + 0.5}", f"{total_shots} remates, jogo aberto", 0.75)
    elif elapsed > 70 and total_shots < (elapsed / 10):
        add_tip("Total de Gols", f"Menos de {total_goals + 1.5}", "Jogo com pouca criação", 0.70)
    
    # Lógica de Escanteios
    if 30 < elapsed < 85:
        if total_corners > (elapsed / 8) + 2:
            add_tip("Escanteios Asiáticos", f"Mais de {total_corners + 1.5}", f"Média alta de cantos ({total_corners})", 0.68)
        elif total_corners < (elapsed / 15) and elapsed > 60:
            add_tip("Escanteios Asiáticos", f"Menos de {total_corners + 2.5}", f"Média baixa de cantos ({total_corners})", 0.65)

    # Lógica de Resultado e Handicap
    pressure_diff = (home_shots_on * 1.5 + home_corners) - (away_shots_on * 1.5 + away_corners)
    if 55 < elapsed < 88 and home_goals == away_goals:
        if pressure_diff > 4:
            add_tip("Próximo Gol", "Casa", f"Casa pressionando mais ({pressure_diff:.1f})", 0.72)
            add_tip("Handicap Asiático", "Casa -0.5", "Casa dominante e busca o gol", 0.68)
        elif pressure_diff < -4:
            add_tip("Próximo Gol", "Visitante", f"Visitante pressionando mais ({pressure_diff:.1f})", 0.72)
            add_tip("Handicap Asiático", "Visitante -0.5", "Visitante dominante e busca o gol", 0.68)
    
    if elapsed > 80:
        if home_goals > away_goals:
            add_tip("Resultado Final", "Vitória Casa", "Casa segurando o resultado", 0.80)
        elif away_goals > home_goals:
            add_tip("Resultado Final", "Vitória Visitante", "Visitante segurando o resultado", 0.80)

    if not tips:
        add_tip("Análise", "Aguardando Oportunidade", "Nenhum mercado com valor claro no momento", 0.30)
    
    tips.sort(key=lambda x: x['confidence'], reverse=True)
    return tips[:3]

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
    ck = f"radar_stats_{game_id}_full_v2"
    cached = _radar_cache_get(ck)
    if cached is not None:
        return cached
    try:
        fixture_resp = api_get_raw("fixtures", params={"id": game_id})
        if not fixture_resp or not fixture_resp.get("response"):
            print(f"ERRO: Fixture {game_id} não encontrado em radar_ia.")
            return None
        fixture = fixture_resp["response"][0]
        home_id = fixture.get("teams", {}).get("home", {}).get("id")

        stats_resp = api_get_raw("fixtures/statistics", params={"fixture": game_id})
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

        events_resp = api_get_raw("fixtures/events", params={"fixture": game_id})
        events = events_resp.get("response", []) if events_resp else []
        processed = []
        for ev in events:
            t = ev.get("time", {})
            elapsed, extra = t.get("elapsed"), t.get("extra")
            _sort_key = (elapsed or 0) + (extra or 0)
            processed.append({
                "display_time": f"{elapsed}+{extra}'" if extra else f"{elapsed}'",
                "category": classify_event(ev),
                "detail": ev.get("detail"),
                "player": ev.get("player", {}).get("name"),
                "_sort": _sort_key
            })
        processed.sort(key=lambda x: x["_sort"], reverse=True)

        result = {
            "fixture": fixture,
            "teams": fixture.get("teams", {}),
            "score": fixture.get("score", {}),
            "goals": fixture.get("goals", {}),
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
    params = {'team': team_id, 'season': season}
    data = api_get_raw("players", params=params)
    if not data or not data.get("response"):
        return None
    players = []
    for item in data['response']:
        p = item.get('player', {}) or {}
        players.append({"id": p.get("id"), "name": p.get("name")})
    return players

# ALTERADO: Removido 'Key Passes' e refinado as recomendações
def process_and_analyze_stats(player_data: Dict) -> Dict:
    stats_list = player_data.get("statistics", []) or []
    aggregated = defaultdict(lambda: defaultdict(float))
    total_games = 0

    for entry in stats_list:
        games_block = entry.get("games", {}) or {}
        appearances = safe_int(games_block.get("appearences", 0) or games_block.get("appearances", 0) or games_block.get("played", 0))
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
        return {"key_stats": {}, "recommendations": [{"market": "N/A", "recommendation": "Dados insuficientes", "confidence": 0, "reason": "Jogador sem partidas"}]}

    avg_goals = (aggregated.get('goals', {}).get('total', 0)) / total_games
    avg_assists = (aggregated.get('goals', {}).get('assists', 0)) / total_games
    avg_shots_total = (aggregated.get('shots', {}).get('total', 0)) / total_games
    avg_shots_on = (aggregated.get('shots', {}).get('on', 0)) / total_games
    avg_passes = (aggregated.get('passes', {}).get('total', 0)) / total_games

    key_stats = {
        "Jogos": f"{int(total_games)}",
        "Gols (média)": f"{avg_goals:.2f}",
        "Assistências (média)": f"{avg_assists:.2f}",
        "Remates (média)": f"{avg_shots_total:.2f}",
        "Remates no Gol (média)": f"{avg_shots_on:.2f}",
        "Passes (média)": f"{avg_passes:.2f}"
    }

    recs = []
    if avg_goals > 0.4:
        recs.append({"market": "Jogador para Marcar", "recommendation": "Sim", "confidence": min(0.8, avg_goals), "reason": f"Média de {avg_goals:.2f} gols/jogo."})
    if avg_shots_on > 1.0:
        recs.append({"market": "Remates no Gol", "recommendation": "Mais de 0.5", "confidence": min(0.75, avg_shots_on / 1.5), "reason": f"Média de {avg_shots_on:.2f} remates no alvo."})
    if avg_shots_total > 2.0:
        recs.append({"market": "Total de Remates", "recommendation": "Mais de 1.5", "confidence": min(0.70, avg_shots_total / 3), "reason": f"Média de {avg_shots_total:.2f} remates totais."})
    if avg_passes > 40:
        recs.append({"market": "Passes do Jogador", "recommendation": f"Mais de {int(avg_passes - 10)}", "confidence": 0.6, "reason": f"Média alta de {avg_passes:.2f} passes."})

    if not recs:
        recs.append({"market": "Análise", "recommendation": "Sem recomendação clara", "confidence": 0.2, "reason": "Baixa participação ofensiva."})

    return {"key_stats": key_stats, "recommendations": recs}

def analyze_player(player_id: int, season: int = datetime.now().year) -> Optional[Dict]:
    try:
        data = api_get_raw("players", params={'id': player_id, 'season': season})
        if not data or not data.get("response"):
            return None
        player_data = data["response"][0]
        player_info = {
            "name": player_data.get('player', {}).get('name'),
            "team": (player_data.get('statistics', [{}])[0].get('team', {}) or {}).get('name'),
        }
        analysis = process_and_analyze_stats(player_data)
        return {"player_info": player_info, **analysis}
    except Exception as e:
        print(f"ERRO ao analisar jogador {player_id}: {e}")
        return None

def analyze_player_stats(player_id: int, season: int = datetime.now().year):
    return analyze_player(player_id, season)

# =========================
# FORMATAÇÃO DAS SAÍDAS
# =========================
# ALTERADO: Formatação do jogador para remover Key Passes e simplificar
def format_player_analysis(player_analysis: dict) -> str:
    if not player_analysis or not player_analysis.get("player_info"):
        return "❌ Não foi possível obter análise para este jogador."
    p_info = player_analysis["player_info"]
    lines = [f"👤 *{p_info.get('name')}* ({p_info.get('team')})"]
    if key_stats := player_analysis.get("key_stats"):
        lines.append("\n📊 *Estatísticas principais*:")
        for k, v in key_stats.items():
            lines.append(f"- {k}: {v}")
    if recs := player_analysis.get("recommendations"):
        lines.append("\n💡 *Recomendações:*")
        for r in recs:
            lines.append(f"- *{r['market']}*: {r['recommendation']} (conf: {format_conf_pct(r['confidence'])}) — {r['reason']}")
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
            if pick.get("reason"): line += f" — {pick.get('reason')}"
            if pick.get("best_book") and pick.get("best_odd"): line += f" [{pick['best_book']} @ {pick['best_odd']:.2f}]"
            lines.append(line)
    lines.append("\n👤 *OptaIA — Jogadores em Destaque*")
    if not players_analysis:
        lines.append("_Nenhuma análise de jogador disponível._")
    else:
        for player_result in players_analysis:
            if player_result and player_result.get("player_info"):
                p_info = player_result["player_info"]
                recs = player_result.get("recommendations", [])
                lines.append(f"\n*{p_info.get('name')}* ({p_info.get('team')})")
                if not recs:
                    lines.append("  - Sem dicas de aposta específicas.")
                else:
                    for rec in recs:
                        conf_txt = format_conf_pct(rec.get("confidence"))
                        lines.append(f"  - *{rec.get('market')}*: {rec.get('recommendation')} (conf: {conf_txt}) — {rec.get('reason','')}")
    lines.append("\n_Lembre-se: analise por conta própria — estas são sugestões automáticas._")
    return "\n".join(lines)
    
# NOVO: Formatação específica para o RADAR (só estatísticas)
def format_radar_only(radar_data: Dict) -> str:
    if not radar_data: return "❌ Dados do Radar indisponíveis."

    teams = radar_data.get("teams", {})
    home, away = teams.get("home", {}).get("name", "Casa"), teams.get("away", {}).get("name", "Fora")
    status = radar_data.get("status", {})
    score = radar_data.get("goals", {})
    home_goals, away_goals = score.get("home", 0), score.get("away", 0)
    
    lines = [f"📡 *Radar IA — {home} vs {away}*"]
    lines.append(f"⏱️ Minuto: {status.get('elapsed', 0)}'")
    lines.append(f"🔢 Placar: {home} {home_goals} x {away_goals} {away}")
    lines.append("--------------------------------------------")
    lines.append("📊 *Estatísticas do Jogo*")
    
    stats = radar_data.get("statistics", {})
    home_stats, away_stats = stats.get("home", {}), stats.get("away", {})
    def gs(s, *k):
        for key in k:
            if key in s: return s.get(key, 0)
        return 0

    lines.append(f"- Remates: {gs(home_stats, 'total_shots')} x {gs(away_stats, 'total_shots')}")
    lines.append(f"- Remates no Gol: {gs(home_stats, 'shots_on_goal')} x {gs(away_stats, 'shots_on_goal')}")
    lines.append(f"- Escanteios: {gs(home_stats, 'corner_kicks')} x {gs(away_stats, 'corner_kicks')}")
    lines.append(f"- Cartões Amarelos: {gs(home_stats, 'yellow_cards')} x {gs(away_stats, 'yellow_cards')}")
    lines.append(f"- Posse de Bola: {gs(home_stats, 'ball_possession', 'possession')}% x {gs(away_stats, 'ball_possession', 'possession')}%")

    # Lógica de estimativa de acréscimos pode ser adicionada aqui
    
    lines.append("\n_Radar fornece apenas estatísticas, sem dicas de aposta._")
    return "\n".join(lines)

# ALTERADO: Formatação da análise ao vivo com a estrutura que você pediu
def format_live_analysis(radar_data: Dict, tips: List[Dict]) -> str:
    if not radar_data: return "❌ Dados da análise ao vivo indisponíveis."

    teams = radar_data.get("teams", {})
    home, away = teams.get("home", {}).get("name", "Casa"), teams.get("away", {}).get("name", "Fora")
    status = radar_data.get("status", {})
    score = radar_data.get("goals", {})
    home_goals, away_goals = score.get("home", 0), score.get("away", 0)

    lines = [f"📊 *Análise ao vivo — {home} vs {away}*"]
    lines.append(f"⏱️ Minuto: {status.get('elapsed', 0)}'")
    lines.append(f"🔢 Placar: {home} {home_goals} x {away_goals} {away}")
    lines.append("--------------------------------------------")
    lines.append("📊 *Estatísticas do Jogo*")

    stats = radar_data.get("statistics", {})
    home_stats, away_stats = stats.get("home", {}), stats.get("away", {})
    def gs(s, *k):
        for key in k:
            if key in s: return s.get(key, 0)
        return 0

    lines.append(f"- Remates: {gs(home_stats, 'total_shots')} x {gs(away_stats, 'total_shots')}")
    lines.append(f"- Remates no Gol: {gs(home_stats, 'shots_on_goal')} x {gs(away_stats, 'shots_on_goal')}")
    lines.append(f"- Escanteios: {gs(home_stats, 'corner_kicks')} x {gs(away_stats, 'corner_kicks')}")

    lines.append("--------------------------------------------")
    lines.append("🎯 *Dicas de Aposta (Top 3)*")

    if not tips or tips[0]['market'] == 'Análise':
        lines.append(f"_{tips[0]['reason']}_" if tips else "_Aguardando oportunidade clara..._")
    else:
        for tip in tips:
            line = f"- *{tip['market']}*: {tip['recommendation']} (conf: {format_conf_pct(tip['confidence'])}) — {tip['reason']}"
            if tip.get("best_odd") and tip.get("best_book"):
                 line += f" — 💰 {tip['best_odd']:.2f} @ {tip['best_book']}"
            lines.append(line)
            
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

def _make_game_obj_from_fixture(f: dict) -> dict:
    fd = f.get("fixture", {}) or {}
    fid = fd.get("id")
    league = f.get("league", {}) or {}
    teams = f.get("teams", {}) or {}
    label = f"{teams.get('home',{}).get('name')} vs {teams.get('away',{}).get('name')}"
    return {
        "game_id": fid,
        "label": label,
        "utc_date": fd.get("date"),
        "league": { "id": league.get("id"), "country": league.get("country"), "name": league.get("name") },
        "raw": f
    }

def group_fixtures_by_country_league(fixtures: List[dict]) -> List[Dict[str, Any]]:
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
    out = []
    for country, cdata in buckets.items():
        leagues_list = []
        for lid, ldata in cdata["leagues"].items():
            leagues_list.append(ldata)
        out.append({"country": country, "leagues": leagues_list})
    return out

@app.route("/pre-live-games", methods=["GET"])
def pre_live_games_compat():
    fixtures = get_fixtures_for_dates(days_forward=0)
    out = []
    for f in fixtures:
        if f.get("type") == "scheduled":
            raw = f.get("raw") or {}
            out.append(_make_game_obj_from_fixture(raw))
    return jsonify(out), 200

@app.route("/pre-live-games/full", methods=["GET"])
def pre_live_games_full():
    fixtures = get_fixtures_for_dates(days_forward=0)
    out = []
    for f in fixtures:
        if f.get("type") == "scheduled":
            out.append(f.get("raw"))
    return jsonify({"response": out}), 200

@app.route("/pre-live-grouped", methods=["GET"])
def pre_live_grouped():
    fixtures = get_fixtures_for_dates(days_forward=0)
    raws = [f.get("raw") for f in fixtures if f.get("type") == "scheduled"]
    if not raws:
        return jsonify([]), 200
    grouped = group_fixtures_by_country_league(raws)
    return jsonify(grouped), 200

@app.route("/live-games", methods=["GET"])
def live_games_compat():
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
                            if p_analysis: players_analysis.append(p_analysis)
                        if len(players) > 1:
                            pid2 = players[1].get("id")
                            if pid2:
                                p2_analysis = analyze_player(pid2)
                                if p2_analysis: players_analysis.append(p2_analysis)
                if away_id and len(players_analysis) < 2:
                    players = get_players_for_team(away_id) or []
                    if players:
                        pid = players[0].get("id")
                        if pid:
                            p_analysis = analyze_player(pid)
                            if p_analysis: players_analysis.append(p_analysis)
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
        if not radar_data:
             return jsonify({"analysis_text": "❌ Não foi possível obter dados ao vivo do jogo."})
        live_tips = analyze_live_from_stats(radar_data)
        enhanced_tips = enhance_predictions_with_odds(live_tips, int(game_id))
        text = format_live_analysis(radar_data, enhanced_tips)
        return jsonify({"analysis_text": text, "raw": {"radar": radar_data, "tips": enhanced_tips}}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# CORRIGIDO: Endpoint do Radar agora usa a formatação correta (sem dicas)
@app.route("/analyze/radar", methods=["POST"])
def api_analyze_radar():
    try:
        data = request.get_json() or {}
        game_id = data.get("game_id")
        if not game_id:
            return jsonify({"error": "game_id é obrigatório"}), 400
        radar_data = stats_aovivo(int(game_id))
        if not radar_data:
            return jsonify({"analysis_text": "❌ Não foi possível obter dados do radar."})
        text = format_radar_only(radar_data) # <--- CORREÇÃO APLICADA AQUI
        return jsonify({"analysis_text": text, "raw": radar_data}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# (Rotas Opta mantidas integralmente)
@app.route("/players", methods=["GET"])
def api_players_old():
    try:
        player_id = request.args.get("id") or request.args.get("player_id")
        season = request.args.get("season") or datetime.now().year
        if not player_id: return jsonify({"error": "id do jogador é obrigatório"}), 400
        analysis = analyze_player(int(player_id), int(season))
        if analysis is None: return jsonify({"error": "Nenhum dado encontrado"}), 404
        analysis_text = format_player_analysis(analysis)
        return jsonify({"opta": {**analysis, "analysis_text": analysis_text}}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/opta-player", methods=["POST"])
def api_opta_player_post():
    try:
        data = request.get_json() or {}
        player_id = data.get("player_id")
        season = data.get("season") or datetime.now().year
        if not player_id: return jsonify({"error": "player_id é obrigatório"}), 400
        analysis = analyze_player(int(player_id), int(season))
        if analysis is None: return jsonify({"error": "Nenhum dado encontrado"}), 404
        analysis_text = format_player_analysis(analysis)
        return jsonify({"opta": {**analysis, "analysis_text": analysis_text}}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/opta/leagues", methods=["GET"])
def opta_leagues():
    try:
        fixtures = get_fixtures_for_dates(days_forward=2)
        leagues_map = {}
        for f in fixtures:
            raw = f.get("raw") or {}
            league = raw.get("league", {}) or {}
            lid = league.get("id")
            if not lid: continue
            leagues_map[lid] = {"id": lid, "name": league.get("name"), "country": league.get("country")}
        out = list(leagues_map.values())
        return jsonify(out), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify([]), 200

@app.route("/opta/teams", methods=["GET"])
def opta_teams():
    try:
        league_id = request.args.get("league_id")
        if not league_id: return jsonify({"error": "league_id obrigatório"}), 400
        fixtures = get_fixtures_for_dates(days_forward=2)
        teams_map = {}
        for f in fixtures:
            raw = f.get("raw") or {}
            league = raw.get("league", {}) or {}
            if str(league.get("id")) != str(league_id): continue
            teams = raw.get("teams", {}) or {}
            home = teams.get("home", {}) or {}
            away = teams.get("away", {}) or {}
            if home.get("id"): teams_map[home.get("id")] = {"id": home.get("id"), "name": home.get("name")}
            if away.get("id"): teams_map[away.get("id")] = {"id": away.get("id"), "name": away.get("name")}
        out = list(teams_map.values())
        return jsonify(out), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify([]), 200

@app.route("/opta/players", methods=["GET"])
def opta_players():
    try:
        team_id = request.args.get("team_id")
        season = request.args.get("season") or datetime.now().year
        if not team_id: return jsonify({"error": "team_id obrigatório"}), 400
        players = get_players_for_team(int(team_id), int(season)) or []
        return jsonify(players), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify([]), 200

# =========================
# RUN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Tipster API rodando na porta {port} (modo local)")
    app.run(host="0.0.0.0", port=port, debug=True)
