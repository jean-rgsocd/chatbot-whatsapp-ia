# radar_ia.py (Módulo de Análise Ao Vivo)
# Versão limpa, sem servidor e sem chaves de API fixas.

import requests
import os
import traceback
import time

# --- Configuração ---
API_KEY = os.environ.get("API_SPORTS_KEY") # Pega a chave do ambiente, mais seguro.
API_CFG = {"football": {"base": "https://v3.football.api-sports.io", "host": "v3.football.api-sports.io"}}

CACHE_TTL = 8
_cache = {}

# --- Helpers ---
def _cache_get(key):
    rec = _cache.get(key)
    if not rec:
        return None
    if time.time() - rec.get("ts", 0) > CACHE_TTL:
        _cache.pop(key, None)
        return None
    return rec["data"]

def _cache_set(key, data):
    _cache[key] = {"ts": time.time(), "data": data}

def headers_for():
    cfg = API_CFG["football"]
    return {"x-apisports-key": API_KEY, "x-rapidapi-host": cfg["host"]}

def safe_get(url, headers, params=None, timeout=20):
    if not API_KEY:
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

# --- Função Principal de Análise Ao Vivo (usada pelo app.py) ---
def stats_aovivo(game_id: int):
    ck = f"radar_stats_{game_id}_full"
    cached = _cache_get(ck)
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
        _cache_set(ck, result)
        return result
    except Exception:
        traceback.print_exc()
        return None
