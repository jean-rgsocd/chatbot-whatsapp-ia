# opta_ia.py (Módulo de Análise de Jogadores)
# Versão limpa, sem servidor e sem chaves de API fixas.

import requests
import os
from collections import defaultdict
from typing import List, Dict, Optional

# --- Configuração ---
API_KEY = os.environ.get("API_SPORTS_KEY") # Pega a chave do ambiente, mais seguro.
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

# --- Helpers ---
def safe_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (ValueError, TypeError, AttributeError):
        return default

def safe_float(v, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", "."))
    except (ValueError, TypeError, AttributeError):
        return default

# --- Lógica Principal de Análise de Jogador (para uso futuro) ---
def process_and_analyze_stats(player_data: Dict) -> Dict:
    stats_list = player_data.get("statistics", []) or []
    aggregated = defaultdict(lambda: defaultdict(float))
    total_games = 0

    for entry in stats_list:
        games_block = entry.get("games", {}) or {}
        appearances = safe_int(games_block.get("appearences", 0), 0)
        if appearances <= 0:
            continue
        total_games += appearances
        for category, block in entry.items():
            if isinstance(block, dict):
                for k, v in block.items():
                    aggregated[category][k] += safe_float(v, 0.0)

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

def analyze_player(player_id: int, season: int) -> Optional[Dict]:
    if not API_KEY:
        print("ERRO: A variável de ambiente API_SPORTS_KEY não está definida.")
        return None
    try:
        params = {'id': player_id, 'season': season}
        r = requests.get(f"{BASE_URL}/players", headers=HEADERS, params=params, timeout=20)
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
