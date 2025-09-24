# tipster.py — UNIFICADO (Parte 1/3)
# Contém: imports, configuração, cache, HTTP helpers, utilitários,
# sports betting analyzer (pré-live) + odds mapping, radar básico (fetch)
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

API_URL_BASE = os.environ.get("API_URL_BASE", "https://v3.football.api-sports.io")
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
        # Log claro para debugging local
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
    try:
        return int(v)
    except (ValueError, TypeError):
        try:
            return int(float(v))
        except Exception:
            return 0

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

PREFERRED_BOOKMAKERS = [b.lower() for b in (os.environ.get("PREFERRED_BOOKMAKERS", "bet365,betano,superbet,pinnacle").split(","))]

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
    if not h_sot:
        h_sot = g(home_stats, "Total shots") or g(home_stats, "Shots total") or g(home_stats, "Total shots on target") or 0
    if not a_sot:
        a_sot = g(away_stats, "Total shots") or g(away_stats, "Shots total") or g(away_stats, "Total shots on target") or 0
    h_power = h_sot * 1.6; a_power = a_sot * 1.6
    power_diff = h_power - a_power
    preds: List[dict] = []
    def add(market, rec, conf, reason=None):
        preds.append({"market": market, "recommendation": rec, "confidence": conf, "reason": reason or ""})
    # primary picks
    if power_diff > 4:
        add("Resultado Final", "Vitória Casa", 0.80, f"Diferença de força {round(power_diff,2)}")
    elif power_diff < -4:
        add("Resultado Final", "Vitória Visitante", 0.80, f"Diferença de força {round(power_diff,2)}")
    # additional heuristics
    total_shots = (h_sot or 0) + (a_sot or 0)
    if total_shots > 8:
        add("Total de Gols", "Mais de 1.5", 0.60, f"{total_shots} remates detectados")
    if (h_sot or 0) > 2 and (a_sot or 0) > 2:
        add("Ambas Marcam", "Sim", 0.60, f"Remates: {h_sot} vs {a_sot}")
    summary = {"home_power": round(h_power, 2), "away_power": round(a_power, 2)}

    # Ensure at least 3 picks — fill with generic conservative picks if needed
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
        # garante chaves existam
        for pred in predictions:
            pred.setdefault("best_book", None)
            pred.setdefault("best_odd", None)
        return predictions

    try:
        odds_list = odds_raw.get("response", [])
        # best_odds_map: key -> {"odd": float, "bookmaker": str, "preferred_score": int}
        best_odds_map: Dict[str, Dict[str, Any]] = {}

        # percorre cada item de odds — estrutura pode variar conforme fornecedora
        for entry in odds_list:
            # Normalize bookmaker name
            bookmaker = (entry.get("bookmaker", {}) or {}).get("name", "") or (entry.get("bookmaker", {}) or {}).get("title", "")
            bookmaker_norm = (bookmaker or "").strip().lower()
            bets = entry.get("bets", []) or entry.get("markets", []) or []

            for bet in bets:
                market = bet.get("name") or bet.get("key") or ""
                values = bet.get("values", []) or bet.get("outcomes", []) or []
                for v in values:
                    odd_raw = v.get("odd") or v.get("price") or v.get("odds")
                    label = v.get("value") or v.get("label") or v.get("name")
                    if odd_raw is None or label is None:
                        continue
                    odd_f = safe_float(odd_raw, default=0.0)
                    key = f"{market}:{label}"
                    # score to prefer certain bookmakers
                    preferred_score = 1 + (50 if bookmaker_norm in PREFERRED_BOOKMAKERS else 0)
                    # keep the odd with higher value or higher preferred_score if equal
                    prev = best_odds_map.get(key)
                    if not prev:
                        best_odds_map[key] = {"odd": odd_f, "bookmaker": bookmaker_norm, "score": preferred_score}
                    else:
                        # choose by (odd, score)
                        if odd_f > prev["odd"] or (odd_f == prev["odd"] and preferred_score > prev.get("score", 0)):
                            best_odds_map[key] = {"odd": odd_f, "bookmaker": bookmaker_norm, "score": preferred_score}

        # agora aplica no predictions
        for pred in predictions:
            market = pred.get("market") or pred.get("market_name") or ""
            rec = pred.get("recommendation")
            pred.setdefault("best_odd", None)
            pred.setdefault("best_book", None)
            if not market or rec is None:
                continue
            # Tentativa de matching direto: market:recommendation
            key = f"{market}:{rec}"
            # alguns mercados usam nomes diferentes -> tentamos variações simples
            alt_keys = [key, f"{market.lower()}:{rec}", f"{market}:{str(rec).lower()}"]
            found = None
            for k in alt_keys:
                if k in best_odds_map:
                    found = best_odds_map[k]
                    break
            if not found:
                # tentar matching por partes (ex.: "Total de Gols:Mais de 1.5" vs "Total Goals:Over 1.5")
                # simplifica labels e tenta comparar números
                for k, v in best_odds_map.items():
                    mk, lb = k.split(":", 1) if ":" in k else (k, "")
                    if mk and market and mk.strip().lower() == market.strip().lower():
                        # se labels coincidem por substring numérica, aceita
                        if str(rec) in lb or lb in str(rec):
                            found = v
                            break
            if found:
                pred["best_odd"] = float(found["odd"])
                pred["best_book"] = found["bookmaker"]
    except Exception as e:
        print(f"Erro ao mapear odds: {e}")
        # fallback: garante chaves
        for pred in predictions:
            pred.setdefault("best_odd", None)
            pred.setdefault("best_book", None)

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
        while len(top3) < 3:
            top3.append({"market": "Resultado Final", "recommendation": "Sem favorito definido", "confidence": 0.30})
    return {
        "game_id": game_id,
        "summary": summary,
        "predictions": enhanced,
        "top3": top3,
        "raw_fixture": fixture
    }

# shorthand alias kept for compatibility
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

# === FIM PARTE 1/3 ===
# A seguir (Parte 2/3) virão: analyze_live_from_stats (com heurísticas completas),
# formatação de saída (format_live_analysis, format_radar_only, format_full_pre_game_analysis),
# Opta (player analysis expanded), endpoints Flask (API completa) e RUN block.
# tipster.py — Parte 2/3
# Contém: analyze_live_from_stats, format_live_analysis, format_radar_only,
# Opta player analysis (process_and_analyze_stats, analyze_player), format_full_pre_game_analysis, format_player_analysis

from typing import Dict, List, Any, Optional

# =========================
# análise a partir de dados ao vivo (RadarIA) — com dicas (usado por /analyze/live)
# =========================
def analyze_live_from_stats(radar_data: Dict) -> List[Dict]:
    """
    Recebe radar_data (estrutura retornada por stats_aovivo) e retorna uma lista de dicas.
    Heurísticas simples e robustas para múltiplos formatos de estatísticas.
    """
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
                    try:
                        total_seconds += max(int(end - start), 60)
                    except Exception:
                        total_seconds += 60
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
            lines.append(f"- {display_time} — {category} — {player} — {detail}")

    # estimativa de acréscimo (se tiver)
    extra = radar_data.get("extra_time_est")
    if extra:
        lines.append(f"\n⏱️ Estimativa de Acréscimo {extra.get('half')}ºT: {extra.get('minutes')} min")

    lines.append("\n_Obs: este endpoint é *RADAR ONLY* — não fornece dicas de aposta, apenas estatísticas e eventos._")
    return "\n".join(lines)

# =========================
# OPTA IA (análise de jogador) - funções reutilizáveis (complementares)
# Nota: process_and_analyze_stats e analyze_player foram parcialmente exibidas na Parte 1,
# mas mantemos aqui as funções de formatação e garantia de fallback.
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

def format_full_pre_game_analysis(game_analysis: Dict[str, Any]) -> str:
    """
    Formata o texto do pré-jogo, incluindo top3 mercados,
    odds e casa de aposta quando disponíveis.
    Recebe o objeto retornado por analyze(game_id).
    """
    if not game_analysis:
        return "❌ Nenhum dado de análise disponível."

    fixture = game_analysis.get("raw_fixture", {}) or {}
    league = fixture.get("league", {}) or {}
    teams = fixture.get("teams", {}) or {}
    home = teams.get("home", {}).get("name", "Casa")
    away = teams.get("away", {}).get("name", "Fora")
    date = fixture.get("fixture", {}).get("date") or fixture.get("date") or ""

    dt_fmt = ""
    try:
        if date:
            dt_fmt = datetime.fromisoformat(date.replace("Z", "+00:00")).strftime("%d/%m %H:%M")
    except Exception:
        dt_fmt = str(date)

    lines: List[str] = []
    lines.append(f"📋 Pré-jogo — {home} vs {away}")
    if dt_fmt:
        lines.append(f"🗓️ Data: {dt_fmt}")
    if league:
        lines.append(f"🏆 Liga: {league.get('name','')} ({league.get('country','')})")

    lines.append("\n📊 Análise Estatística:")
    summary = game_analysis.get("summary") or {}
    if summary:
        for k, v in summary.items():
            lines.append(f"- {k}: {v}")
    else:
        lines.append("- Sem resumo estatístico disponível.")

    lines.append("\n🎯 Top 3 Mercados Recomendados:")
    top3 = game_analysis.get("top3", []) or []
    if not top3:
        lines.append("- Nenhuma recomendação disponível.")
    else:
        for tip in top3:
            market = tip.get("market", "—")
            rec = tip.get("recommendation", "—")
            conf = format_conf_pct(tip.get("confidence"))
            odd = tip.get("best_odd")
            book = tip.get("best_book")
            if odd and book:
                try:
                    lines.append(f" - {market}: {rec} ({conf}) — 💰 {float(odd):.2f} @ {book}")
                except Exception:
                    lines.append(f" - {market}: {rec} ({conf}) — 💰 {odd} @ {book}")
            elif odd:
                try:
                    lines.append(f" - {market}: {rec} ({conf}) — 💰 {float(odd):.2f}")
                except Exception:
                    lines.append(f" - {market}: {rec} ({conf}) — 💰 {odd}")
            else:
                lines.append(f" - {market}: {rec} ({conf})")

    # players analysis (if present inside game_analysis)
    players = game_analysis.get("players") or []
    if players:
        lines.append("\n👤 Jogadores analisados:")
        for p in players:
            if p and p.get("player_info"):
                pinfo = p["player_info"]
                lines.append(f"- {pinfo.get('name')} ({pinfo.get('team')})")
                ks = p.get("key_stats", {})
                if ks:
                    lines.append("  • " + " | ".join([f"{k}: {v}" for k, v in ks.items()]))
                recs = p.get("recommendations", [])
                for r in recs:
                    lines.append(f"  - {r.get('market')}: {r.get('recommendation')} (conf: {format_conf_pct(r.get('confidence'))}) — {r.get('reason','')}")
    # note: some clients expect players analysis separately; we place here only if present
    lines.append("\n_Lembre-se: analise por conta própria — estas são sugestões automáticas._")
    return "\n".join(lines)

# === FIM PARTE 2/3 ===
# A seguir (Parte 3/3) virá: endpoints Flask completos (analyze/game, analyze/live, radar-only, opta endpoints),
# integrações finais, pequenos ajustes e bloco if __name__ == "__main__".
# tipster.py — Parte 3/3 (final)
# Contém: endpoints restantes, healthcheck, logging mínimo e app.run

import logging
from flask import Flask, request, jsonify
import traceback

# (assume que todas as funções usadas abaixo foram definidas nas partes 1/2:
#  api_get_raw, get_fixtures_for_dates, _make_game_obj_from_fixture, group_fixtures_by_country_league,
#  analyze, analyze_game, analyze_player, stats_aovivo, analyze_live_from_stats,
#  format_live_analysis, format_radar_only, format_full_pre_game_analysis, format_player_analysis,
#  get_players_for_team, etc.)

# Configura logger básico
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tipster")

# Se app já existir (em Partes anteriores), não recriar. Caso contrário, cria.
try:
    app  # type: ignore
except NameError:
    app = Flask(__name__)

# Healthcheck / status simples
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": int(time.time())}), 200

# Reexpose fixtures endpoints if not already present (safe to call even if defined earlier)
@app.route("/fixtures", methods=["GET"])
def fixtures_endpoint_compat():
    try:
        date_param = request.args.get("date")
        if date_param:
            raw = api_get_raw("fixtures", params={"date": date_param})
        else:
            today = datetime.utcnow().date().strftime("%Y-%m-%d")
            raw = api_get_raw("fixtures", params={"date": today})
        if not raw:
            return jsonify({"response": []}), 200
        return jsonify(raw), 200
    except Exception as e:
        logger.exception("erro /fixtures")
        return jsonify({"response": []}), 200

# Live fixtures raw
@app.route("/fixtures/live", methods=["GET"])
def fixtures_live_raw_compat():
    try:
        raw = api_get_raw("fixtures", params={"live": "all"})
        if not raw:
            return jsonify({"response": []}), 200
        return jsonify(raw), 200
    except Exception:
        logger.exception("erro /fixtures/live")
        return jsonify({"response": []}), 200

# Pre-live list (compat)
@app.route("/pre-live-games", methods=["GET"])
def pre_live_games_compat_final():
    try:
        fixtures = get_fixtures_for_dates(days_forward=0)
        out = []
        for f in fixtures:
            if f.get("type") == "scheduled":
                raw = f.get("raw") or {}
                out.append(_make_game_obj_from_fixture(raw))
        return jsonify(out), 200
    except Exception:
        logger.exception("erro /pre-live-games")
        return jsonify([]), 200

# grouped endpoints
@app.route("/pre-live-grouped", methods=["GET"])
def pre_live_grouped_final():
    try:
        fixtures = get_fixtures_for_dates(days_forward=0)
        raws = [f.get("raw") for f in fixtures if f.get("type") == "scheduled"]
        if not raws:
            return jsonify([]), 200
        grouped = group_fixtures_by_country_league(raws)
        return jsonify(grouped), 200
    except Exception:
        logger.exception("erro /pre-live-grouped")
        return jsonify([]), 200

@app.route("/live-games", methods=["GET"])
def live_games_compat_final():
    try:
        raw = api_get_raw("fixtures", params={"live": "all"})
        out = []
        if raw and raw.get("response"):
            for f in raw["response"]:
                out.append(_make_game_obj_from_fixture(f))
        return jsonify(out), 200
    except Exception:
        logger.exception("erro /live-games")
        return jsonify([]), 200

@app.route("/live-games/full", methods=["GET"])
def live_games_full_final():
    try:
        raw = api_get_raw("fixtures", params={"live": "all"})
        return jsonify(raw or {"response": []}), 200
    except Exception:
        logger.exception("erro /live-games/full")
        return jsonify({"response": []}), 200

@app.route("/live-games-grouped", methods=["GET"])
def live_games_grouped_final():
    try:
        raw = api_get_raw("fixtures", params={"live": "all"})
        if not raw or not raw.get("response"):
            return jsonify([]), 200
        grouped = group_fixtures_by_country_league(raw["response"])
        return jsonify(grouped), 200
    except Exception:
        logger.exception("erro /live-games-grouped")
        return jsonify([]), 200

# Analyze game (pré) - endpoint robusto com players
@app.route("/analyze/game", methods=["POST"])
@app.route("/analyze-game", methods=["POST"])
def api_analyze_game_final():
    try:
        data = request.get_json() or {}
        game_id = data.get("game_id")
        if not game_id:
            return jsonify({"error": "game_id é obrigatório"}), 400

        # run analysis
        game_analysis = analyze(int(game_id))
        players_analysis = []

        # attempt to include a couple players analyses (safe failures)
        try:
            if game_analysis and game_analysis.get("raw_fixture"):
                fixture = game_analysis["raw_fixture"]
                home_id = fixture.get("teams", {}).get("home", {}).get("id")
                away_id = fixture.get("teams", {}).get("away", {}).get("id")

                # home first two players
                if home_id:
                    players = get_players_for_team(home_id) or []
                    for i, pl in enumerate(players[:2]):
                        pid = pl.get("id")
                        if pid:
                            pa = analyze_player(int(pid))
                            if pa:
                                players_analysis.append(pa)
                # if still less than 2, try away
                if away_id and len(players_analysis) < 2:
                    players = get_players_for_team(away_id) or []
                    for pl in players[:2]:
                        pid = pl.get("id")
                        if pid:
                            pa = analyze_player(int(pid))
                            if pa:
                                players_analysis.append(pa)
        except Exception:
            logger.exception("erro obtendo players_analysis")

        text = format_full_pre_game_analysis({**(game_analysis or {}), "players": players_analysis})
        return jsonify({"analysis_text": text, "raw": {"game_analysis": game_analysis, "players": players_analysis}}), 200
    except Exception as e:
        logger.exception("erro /analyze/game")
        return jsonify({"error": str(e)}), 500

# Analyze live (usa radar + dicas)
@app.route("/analyze/live", methods=["POST"])
@app.route("/analyze-live-game", methods=["POST"])
def api_analyze_live_final():
    try:
        data = request.get_json() or {}
        game_id = data.get("game_id")
        if not game_id:
            return jsonify({"error": "game_id é obrigatório"}), 400

        radar_data = stats_aovivo(int(game_id))
        if not radar_data:
            return jsonify({"analysis_text": "❌ Não foi possível obter dados do radar."}), 200

        live_tips = analyze_live_from_stats(radar_data or {})
        text = format_live_analysis(radar_data or {}, live_tips)
        return jsonify({"analysis_text": text, "raw": {"radar": radar_data, "tips": live_tips}}), 200
    except Exception as e:
        logger.exception("erro /analyze/live")
        return jsonify({"error": str(e)}), 500

# Radar-only: apenas estatísticas e eventos (sem dicas)
@app.route("/analyze/radar", methods=["POST"])
def api_analyze_radar_final():
    try:
        data = request.get_json() or {}
        game_id = data.get("game_id")
        if not game_id:
            return jsonify({"error": "game_id é obrigatório"}), 400

        radar_data = stats_aovivo(int(game_id))
        if not radar_data:
            return jsonify({"analysis_text": "❌ Não foi possível obter dados do radar."}), 200

        text = format_radar_only(radar_data)
        return jsonify({"analysis_text": text, "raw": {"radar": radar_data}}), 200
    except Exception as e:
        logger.exception("erro /analyze/radar")
        return jsonify({"error": str(e)}), 500

# Opta endpoints (players / assist utils)
@app.route("/players", methods=["GET"])
def api_players_old_final():
    try:
        player_id = request.args.get("id") or request.args.get("player_id")
        season = request.args.get("season") or datetime.now().year
        if not player_id:
            return jsonify({"error": "id do jogador é obrigatório (parâmetro id)"}), 400
        analysis = analyze_player(int(player_id), int(season))
        if analysis is None:
            return jsonify({"error": "Nenhum dado encontrado"}), 404
        analysis_text = format_player_analysis(analysis)
        return jsonify({"opta": {**analysis, "analysis_text": analysis_text}}), 200
    except Exception as e:
        logger.exception("erro /players")
        return jsonify({"error": str(e)}), 500

@app.route("/opta-player", methods=["POST"])
def api_opta_player_post_final():
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
        return jsonify({"opta": {**analysis, "analysis_text": analysis_text}}), 200
    except Exception as e:
        logger.exception("erro /opta-player")
        return jsonify({"error": str(e)}), 500

@app.route("/opta/leagues", methods=["GET"])
def opta_leagues_final():
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
    except Exception:
        logger.exception("erro /opta/leagues")
        return jsonify([]), 200

@app.route("/opta/teams", methods=["GET"])
def opta_teams_final():
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
    except Exception:
        logger.exception("erro /opta/teams")
        return jsonify([]), 200

@app.route("/opta/players", methods=["GET"])
def opta_players_final():
    try:
        team_id = request.args.get("team_id")
        season = request.args.get("season") or datetime.now().year
        if not team_id:
            return jsonify({"error": "team_id obrigatório"}), 400
        players = get_players_for_team(int(team_id), int(season)) or []
        return jsonify(players), 200
    except Exception:
        logger.exception("erro /opta/players")
        return jsonify([]), 200

# Generic error handler (returns JSON)
@app.errorhandler(Exception)
def handle_exception(e):
    logger.exception("Unhandled exception")
    return jsonify({"error": "Internal Server Error", "details": str(e)}), 500

# RUN
if __name__ == "__main__":
    try:
        port = int(os.environ.get("PORT", 5000))
    except Exception:
        port = 5000
    logger.info(f"Tipster API rodando na porta {port} (modo local)")
    # debug True apenas em dev — remova em produção
    app.run(host="0.0.0.0", port=port, debug=True)
