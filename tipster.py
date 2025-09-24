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
    total_shots = (h_sot or 0) + (a_sot or 0)
    if total_shots > 8:
        add("Total de Gols", "Mais de 1.5", 0.6, f"{total_shots} remates detectados")
    if (h_sot or 0) > 2 and (a_sot or 0) > 2:
        add("Ambas Marcam", "Sim", 0.6, f"Remates: {h_sot} vs {a_sot}")
    summary = {"home_power": round(h_power, 2), "away_power": round(a_power, 2)}

    # Ensure at least 3 picks
    if len(preds) < 3:
        if total_shots >= 6:
            add("Total de Gols", "Mais de 1.5", 0.45, "Sugestão baseada em atividade ofensiva")
        else:
            add("Total de Gols", "Menos de 2.5", 0.45, "Sugestão conservadora por baixa atividade")
        add("Ambas Marcam", "Sim", 0.40, "Sugestão conservadora")
    while len(preds) < 3:
        add("Resultado Final", "Sem favorito definido", 0.30, "Dados limitados para definir favorito")

    return preds, summary

def enhance_predictions_with_preferred_odds(predictions: List[Dict], odds_raw: Optional[Dict]) -> List[Dict]:
    """
    Percorre as odds retornadas pela API e tenta associar a melhor odd disponível
    para cada mercado da previsão. Dá prioridade para as casas em PREFERRED_BOOKMAKERS.
    Se não encontrar, mantém None.
    """
    if not odds_raw or not odds_raw.get("response"):
        return predictions

    try:
        odds_list = odds_raw.get("response", [])
        best_odds_map = {}

        for entry in odds_list:
            bookmaker = (entry.get("bookmaker", {}) or {}).get("name", "").lower()
            bets = entry.get("bets", []) or []

            for bet in bets:
                market = bet.get("name") or ""
                values = bet.get("values", []) or []

                for v in values:
                    odd = v.get("odd")
                    label = v.get("value")

                    if not odd or not label:
                        continue

                    key = f"{market}:{label}"
                    odd_f = safe_float(odd)

                    # se ainda não temos esse mercado/label ou se essa odd é melhor
                    if key not in best_odds_map or odd_f > best_odds_map[key]["odd"]:
                        best_odds_map[key] = {
                            "odd": odd_f,
                            "bookmaker": bookmaker
                        }

        # agora aplica no predictions
        for pred in predictions:
            market = pred.get("market")
            rec = pred.get("recommendation")
            if not market or not rec:
                continue

            key = f"{market}:{rec}"
            if key in best_odds_map:
                data = best_odds_map[key]
                pred["best_odd"] = data["odd"]
                pred["best_book"] = data["bookmaker"]
            else:
                pred.setdefault("best_odd", None)
                pred.setdefault("best_book", None)

    except Exception as e:
        print(f"Erro ao mapear odds: {e}")
        for pred in predictions:
            pred.setdefault("best_odd", None)
            pred.setdefault("best_book", None)

    return predictions


def analyze_game(game_id: int):
    return analyze(game_id)

# =========================
# RADAR IA (ao vivo) — funções consolidadas & helpers
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

        # estatísticas
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

        # eventos
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
# análise a partir de dados ao vivo (RadarIA) — com dicas (usado por /analyze/live)
# =========================
def analyze_live_from_stats(radar_data: Dict) -> List[Dict]:
    if not radar_data:
        return []

    tips: List[Dict[str, Any]] = []
    stats = radar_data.get("statistics", {}) or {}
    home_stats = stats.get("home", {}) or {}
    away_stats = stats.get("away", {}) or {}
    status = radar_data.get("status", {}) or {}
    elapsed = int(status.get("elapsed") or 0)

    # Placar atualizado - algumas respostas usam fixture.goals, outras usam score/fulltime
    fixture = radar_data.get("fixture", {}) or {}
    # prefer map under fixture.goals if present
    score = fixture.get("goals") or radar_data.get("score", {}).get("fulltime") or {}
    home_goals = score.get("home") or 0
    away_goals = score.get("away") or 0
    total_goals = (home_goals or 0) + (away_goals or 0)

    def get_stat(side_stats, *keys):
        for k in keys:
            if k in side_stats:
                return side_stats.get(k) or 0
        return 0

    # Remates totais / no alvo
    home_shots_total = get_stat(home_stats, 'total_shots','shots_total','shots')
    away_shots_total = get_stat(away_stats, 'total_shots','shots_total','shots')
    home_shots_on = get_stat(home_stats, 'shots_on_target','shots_on','on_target','shots_on_goal')
    away_shots_on = get_stat(away_stats, 'shots_on_target','shots_on','on_target','shots_on_goal')
    total_shots = (home_shots_total or 0) + (away_shots_total or 0)

    # Escanteios
    home_corners = get_stat(home_stats, 'corner_kicks','corners','corner_kicks_full')
    away_corners = get_stat(away_stats, 'corner_kicks','corners','corner_kicks_full')
    total_corners = (home_corners or 0) + (away_corners or 0)

    def add_tip(market, recommendation, reason, confidence):
        tips.append({
            "market": market,
            "recommendation": recommendation,
            "reason": reason,
            "confidence": confidence
        })

    # Estimativa de acréscimos baseada em events (se existirem)
    def estimate_extra_time(events: list, half: int = 1) -> int:
        total_seconds = 0
        for ev in events:
            minute = ev.get("time", {}).get("elapsed") or 0
            if half == 1 and minute > 45:
                continue
            if half == 2 and minute <= 45:
                continue
            cat = (ev.get("category") or "").lower()
            if "falta" in cat:
                total_seconds += 15
            elif "amarelo" in cat:
                total_seconds += 30
            elif "gol" in cat:
                total_seconds += 60
            elif "var" in cat:
                start = ev.get("start_time"); end = ev.get("end_time")
                if start and end:
                    total_seconds += max(int(end - start), 60)
                else:
                    total_seconds += 60
        return (total_seconds + 59) // 60

    # só armazena no radar_data, não vira dica de aposta
    if 35 <= elapsed < 45:
        radar_data["extra_time_est"] = {
            "half": 1,
            "minutes": estimate_extra_time(radar_data.get("events", []), half=1)
        }
    if 80 <= elapsed < 90:
        radar_data["extra_time_est"] = {
            "half": 2,
            "minutes": estimate_extra_time(radar_data.get("events", []), half=2)
        }

    # Regras / heurísticas de dicas (só aqui — esse retorno vai pro /analyze/live)
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

    # Escanteios heurística: se já tem muitos ou pressão alta com poucos cantos
    if elapsed > 25:
        if total_corners >= 7:
            add_tip("Escanteios Asiáticos", f"Mais de {total_corners + 1.5}",
                    f"{total_corners} escanteios já cobrados", 0.72)
        elif total_shots > 10 and total_corners < 5:
            if (home_shots_total or 0) > (away_shots_total or 0):
                add_tip("Escanteios (Equipe)", "Próximo escanteio para o Time da Casa",
                        "Casa pressiona forte mas tem poucos cantos", 0.65)
            else:
                add_tip("Escanteios (Equipe)", "Próximo escanteio para o Time Visitante",
                        "Visitante pressiona forte mas tem poucos cantos", 0.65)

    # Pressão ofensiva — sugere vitória / próximo gol
    if (home_shots_total or 0) - (away_shots_total or 0) >= 8 and (home_shots_on or 0) >= 3:
        add_tip("Resultado Final", "Vitória do Time da Casa",
                f"Domínio ofensivo: {home_shots_total} x {away_shots_total} remates", 0.78)
        add_tip("Próximo Gol", "Casa",
                f"Alta pressão ofensiva ({home_shots_total} remates, {home_shots_on} no alvo)", 0.75)
    elif (away_shots_total or 0) - (home_shots_total or 0) >= 8 and (away_shots_on or 0) >= 3:
        add_tip("Resultado Final", "Vitória do Time Visitante",
                f"Domínio ofensivo: {away_shots_total} x {home_shots_total} remates", 0.78)
        add_tip("Próximo Gol", "Visitante",
                f"Alta pressão ofensiva ({away_shots_total} remates, {away_shots_on} no alvo)", 0.75)

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
# formatação da análise para exibição (com dicas) — usado por /analyze/live
# =========================
def format_live_analysis(radar_data: Dict, tips: List[Dict]) -> str:
    lines: List[str] = []
    status = radar_data.get("status", {}) or {}
    fixture = radar_data.get("fixture", {}) or {}
    teams = radar_data.get("teams", {}) or {}
    score = radar_data.get("score", {}).get("fulltime", {}) or {}
    goals = radar_data.get("goals", {}) or {}

    home = teams.get("home", {}).get("name", "Casa")
    away = teams.get("away", {}).get("name", "Fora")
    home_goals = goals.get("home") or score.get("home") or 0
    away_goals = goals.get("away") or score.get("away") or 0

    lines.append(f"📊 Análise ao vivo — {home} vs {away}")
    lines.append(f"⏱️ Minuto: {status.get('elapsed', 0)}'")
    lines.append(f"🔢 Placar: {home} {home_goals} x {away_goals} {away}")

    # estimativa de acréscimo (se tiver)
    extra_est = radar_data.get("extra_time_est")
    if extra_est:
        lines.append(f"⏱️ Estimativa de Acréscimo {extra_est['half']}ºT: {extra_est['minutes']} min")

    # estatísticas principais
    stats = radar_data.get("statistics", {}) or {}
    home_stats = stats.get("home", {}) or {}
    away_stats = stats.get("away", {}) or {}

    def get_stat_display(side_stats, *keys):
        for k in keys:
            if k in side_stats:
                return side_stats.get(k) or 0
        return 0

    lines.append("\n📊 Estatísticas principais:")
    lines.append(
        f"Remates: {get_stat_display(home_stats, 'total_shots','shots_total','shots')} x {get_stat_display(away_stats, 'total_shots','shots_total','shots')}"
    )
    lines.append(
        f"Remates no Gol: {get_stat_display(home_stats, 'shots_on_target','shots_on','on_target','shots_on_goal')} x {get_stat_display(away_stats, 'shots_on_target','shots_on','on_target','shots_on_goal')}"
    )
    lines.append(
        f"Escanteios: {get_stat_display(home_stats, 'corner_kicks','corners','corner_kicks_full')} x {get_stat_display(away_stats, 'corner_kicks','corners','corner_kicks_full')}"
    )
    lines.append(
        f"Cartões Amarelos: {get_stat_display(home_stats, 'yellow_cards','yellows','cards_yellow')} x {get_stat_display(away_stats, 'yellow_cards','yellows','cards_yellow')}"
    )
    lines.append(
        f"Cartões Vermelhos: {get_stat_display(home_stats, 'red_cards','reds','cards_red')} x {get_stat_display(away_stats, 'red_cards','reds','cards_red')}"
    )
    lines.append(
        f"Posse de Bola: {get_stat_display(home_stats, 'ball_possession','possession','possession_pct')}% x {get_stat_display(away_stats, 'ball_possession','possession','possession_pct')}%"
    )

    # dicas de aposta (render)
    lines.append("\n🎯 Dicas de Aposta:")
    grouped = {"Gols": [], "Resultado": [], "Próximo Gol": [], "Escanteios": [], "Outros": []}
    for t in tips:
        m = t.get("market","")
        if "Gol" in m:
            if "Próximo" in m or t.get("market") == "Próximo Gol":
                grouped["Próximo Gol"].append(t)
            else:
                grouped["Gols"].append(t)
        elif "Resultado" in m:
            grouped["Resultado"].append(t)
        elif "Escanteio" in m or "Escanteios" in m:
            grouped["Escanteios"].append(t)
        else:
            grouped["Outros"].append(t)

    if grouped["Gols"]:
        lines.append("\n⚽ Mercados de Gols:")
        for tip in grouped["Gols"]:
            lines.append(f" - {tip['market']}: {tip['recommendation']} ({tip['reason']}) [{tip['confidence']*100:.0f}%]")

    if grouped["Resultado"]:
        lines.append("\n🏆 Resultado Final:")
        for tip in grouped["Resultado"]:
            lines.append(f" - {tip['recommendation']} ({tip['reason']}) [{tip['confidence']*100:.0f}%]")

    if grouped["Próximo Gol"]:
        lines.append("\n🔮 Próximo Gol:")
        for tip in grouped["Próximo Gol"]:
            lines.append(f" - {tip['recommendation']} ({tip['reason']}) [{tip['confidence']*100:.0f}%]")

    if grouped["Escanteios"]:
        lines.append("\n🥅 Escanteios:")
        for tip in grouped["Escanteios"]:
            lines.append(f" - {tip['market']}: {tip['recommendation']} ({tip['reason']}) [{tip['confidence']*100:.0f}%]")

    if grouped["Outros"]:
        lines.append("\n📌 Outros:")
        for tip in grouped["Outros"]:
            lines.append(f" - {tip['market']}: {tip['recommendation']} ({tip['reason']}) [{tip['confidence']*100:.0f}%]")

    return "\n".join(lines)

# =========================
# format_radar_only (SEM dicas) — usado por /analyze/radar
# =========================
def format_radar_only(radar_data: Dict) -> str:
    lines: List[str] = []
    status = radar_data.get("status", {}) or {}
    fixture = radar_data.get("fixture", {}) or {}
    teams = radar_data.get("teams", {}) or {}
    stats = radar_data.get("statistics", {}) or {}
    events = radar_data.get("events", []) or []

    home = teams.get("home", {}).get("name", "Casa")
    away = teams.get("away", {}).get("name", "Fora")
    score_map = fixture.get("goals") or radar_data.get("score", {}).get("fulltime", {}) or {}
    home_goals = score_map.get("home") or 0
    away_goals = score_map.get("away") or 0

    lines.append(f"📡 Radar — {home} vs {away}")
    lines.append(f"⏱️ Minuto: {status.get('elapsed', 0)}'")
    lines.append(f"🔢 Placar: {home} {home_goals} x {away_goals} {away}")

    # estatísticas (display-friendly)
    def get_stat_display(side_stats, *keys):
        for k in keys:
            if k in side_stats:
                return side_stats.get(k) or 0
        return 0

    home_stats = stats.get("home", {}) or {}
    away_stats = stats.get("away", {}) or {}

    lines.append("\n📊 Estatísticas principais (Radar):")
    lines.append(f"- Remates: {get_stat_display(home_stats, 'total_shots','shots_total','shots')} x {get_stat_display(away_stats, 'total_shots','shots_total','shots')}")
    lines.append(f"- Remates no Gol: {get_stat_display(home_stats, 'shots_on_target','shots_on','on_target','shots_on_goal')} x {get_stat_display(away_stats, 'shots_on_target','shots_on','on_target','shots_on_goal')}")
    lines.append(f"- Escanteios: {get_stat_display(home_stats, 'corner_kicks','corners','corner_kicks_full')} x {get_stat_display(away_stats, 'corner_kicks','corners','corner_kicks_full')}")
    lines.append(f"- Cartões Amarelos: {get_stat_display(home_stats, 'yellow_cards','yellows','cards_yellow')} x {get_stat_display(away_stats, 'yellow_cards','yellows','cards_yellow')}")
    lines.append(f"- Cartões Vermelhos: {get_stat_display(home_stats, 'red_cards','reds','cards_red')} x {get_stat_display(away_stats, 'red_cards','reds','cards_red')}")
    lines.append(f"- Posse de Bola: {get_stat_display(home_stats, 'ball_possession','possession','possession_pct')}% x {get_stat_display(away_stats, 'ball_possession','possession','possession_pct')}%")

    # eventos (mais recentes primeiro já processados)
    lines.append("\n📝 Eventos recentes:")
    if not events:
        lines.append("_Nenhum evento registrado no Radar._")
    else:
        # events already processed in stats_aovivo with fields display_time, category, detail, player
        for ev in events[:18]:  # limitar para não inundar
            player = ev.get("player") or ""
            detail = ev.get("detail") or ""
            display_time = ev.get("display_time") or "-"
            category = ev.get("category") or ""
            # ex.: "45+1' — Gol — Player Name — detalhe"
            lines.append(f"- {display_time} — {category} — {player} — {detail}")

    # estimativa de acréscimo (se tiver)
    extra = radar_data.get("extra_time_est")
    if extra:
        lines.append(f"\n⏱️ Estimativa de Acréscimo {extra.get('half')}ºT: {extra.get('minutes')} min")

    lines.append("\n_Obs: este endpoint é *RADAR ONLY* — não fornece dicas de aposta, apenas estatísticas e eventos._")
    return "\n".join(lines)

# =========================
# OPTA IA (análise de jogador) - expandida (mantive aqui a partir da Parte 1)
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

    def get_stat(cat, key):
        return aggregated.get(cat, {}).get(key, 0.0)

    goals_total = get_stat('goals', 'total') or get_stat('goals', 'goals') or 0.0
    assists_total = get_stat('goals', 'assists') or get_stat('goals', 'assists_total') or 0.0

    shots_total = get_stat('shots', 'total') or get_stat('shots', 'shots_total') or get_stat('shots', 'shots') or 0.0
    shots_on = get_stat('shots', 'on') or get_stat('shots', 'on_target') or get_stat('shots', 'shots_on') or 0.0
    shots_blocked = get_stat('shots', 'blocked') or get_stat('shots', 'blocks') or 0.0
    shots_off = shots_total - shots_on if shots_total and shots_on else get_stat('shots', 'off') or 0.0

    passes_total = get_stat('passes', 'total') or 0.0
    key_passes = get_stat('passes', 'key') or get_stat('passes', 'key_passes') or 0.0

    rebounds_raw = get_stat('rebounds', 'total') or get_stat('shots', 'rebounds') or 0.0
    if not rebounds_raw:
        rebounds_raw = shots_blocked or 0.0

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
    if avg_goals > 0.35:
        rec_conf = min(0.95, avg_goals / 0.7)
        recommendations.append({
            "market": "Jogador para Marcar",
            "recommendation": "Sim",
            "confidence": rec_conf,
            "reason": f"Média de {avg_goals:.2f} gol(s) por jogo nas últimas aparições."
        })
    else:
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

    if avg_assists >= 0.25:
        recommendations.append({
            "market": "Assistências (jogador)",
            "recommendation": "Acima de 0.5",
            "confidence": min(0.65, avg_assists / 0.5),
            "reason": f"Média de {avg_assists:.2f} assistências por jogo."
        })

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

    if avg_rebounds >= 0.3:
        recommendations.append({
            "market": "Rebotes/Remates bloqueados (estim.)",
            "recommendation": "Acima de 0.5",
            "confidence": min(0.45, avg_rebounds / 1),
            "reason": f"Estimativa de {avg_rebounds:.2f} por jogo (base em remates bloqueados/estatísticas disponíveis)."
        })

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

# =========================
# FORMATAÇÃO das saídas textuais (WhatsApp-friendly)
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

# =========================
# FLASK API & Endpoints
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

# fixtures endpoints (compat)
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

@app.route("/fixtures/live", methods=["GET"])
def fixtures_live_raw():
    raw = api_get_raw("fixtures", params={"live": "all"})
    if not raw:
        return jsonify({"response": []}), 200
    return jsonify(raw), 200

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
        "league": {
            "id": league.get("id"),
            "country": league.get("country"),
            "name": league.get("name")
        },
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

# analyze endpoints
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
        if not radar_data:
            return jsonify({"analysis_text": "❌ Não foi possível obter dados do radar."}), 200
        # aqui usamos analyze_live_from_stats (gera dicas) e format_live_analysis
        live_tips = analyze_live_from_stats(radar_data or {})
        text = format_live_analysis(radar_data or {}, live_tips)
        return jsonify({"analysis_text": text, "raw": {"radar": radar_data, "tips": live_tips}}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# radar-only endpoint (for explicit radar menu) — NÃO retorna dicas
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

        # NÃO chama analyze_live_from_stats aqui — só exibe radar
        text = format_radar_only(radar_data)

        return jsonify({
            "analysis_text": text,
            "raw": {
                "radar": radar_data
            }
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Opta endpoints (players)
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

@app.route("/opta/leagues", methods=["GET"])
def opta_leagues():
    try:
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

# =========================
# RUN
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Tipster API rodando na porta {port} (modo local)")
    app.run(host="0.0.0.0", port=port, debug=True)
