# opta ia (Flask) - v5.1 (corrigido, robusto, busca odds de jogadores)
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import datetime
import os
from collections import defaultdict
from typing import List, Dict, Tuple, Optional

# --- Configuração do Servidor Flask ---
app = Flask(__name__)
CORS(app)  # no deploy troque por configuração mais restrita se quiser

# --- CONFIGURAÇÃO DA API DE ESPORTES ---
API_KEY = os.environ.get("API_SPORTS_KEY", "7baa5e00c8ae57d0e6240f790c6840dd")
API_HOST = "v3.football.api-sports.io"
BASE_URL = f"https://{API_HOST}"
HEADERS = {
    "x-apisports-key": API_KEY
}

# --- Helpers seguros ---
def safe_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return default

def safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        try:
            return float(str(v).replace(",", "."))
        except Exception:
            return default

def normalize_str(x) -> str:
    if x is None:
        return ""
    try:
        return str(x).strip()
    except:
        return ""

# ---------------- Processamento e Heurísticas (player) ----------------
def process_and_analyze_stats(player_data: Dict) -> Dict:
    """
    Recebe o objeto de player retornado pela API (o item de response[0])
    e agrega as estatísticas disponíveis, retornando key_stats + recommendations.
    """
    stats_list = player_data.get("statistics", []) or []
    # aggregated structure
    aggregated = defaultdict(lambda: defaultdict(float))
    total_games = 0
    total_minutes = 0
    weighted_rating = 0.0

    for entry in stats_list:
        games_block = entry.get("games", {}) or {}
        # 'appearences' ou 'appearances' - lidamos com ambos
        appearances = games_block.get("appearences") if "appearences" in games_block else games_block.get("appearances")
        appearances = safe_int(appearances, 0)
        minutes = safe_int(games_block.get("minutes", 0), 0)
        rating = safe_float(games_block.get("rating", 0), 0.0)

        if appearances <= 0:
            # se não tiver 'appearences' tente contar como 1 para evitar ignorar totalmente (mas preferimos 0)
            # aqui preferimos pular se zero para evitar distorção
            continue

        total_games += appearances
        total_minutes += minutes
        if rating > 0 and minutes > 0:
            weighted_rating += rating * minutes

        # agrega categorias numéricas conhecidas
        for category, block in entry.items():
            if not isinstance(block, dict):
                continue
            for k, v in block.items():
                if isinstance(v, (int, float)):
                    aggregated[category][k] += v
                else:
                    # tenta converter string numérica
                    try:
                        aggregated[category][k] += float(str(v).replace(",", "."))
                    except:
                        pass

    if total_games == 0:
        return {"key_stats": {}, "recommendations": []}

    def get_stat(cat, key):
        return aggregated.get(cat, {}).get(key, 0.0)

    key_stats = {
        "Rating Médio": f"{(weighted_rating / total_minutes):.2f}" if total_minutes > 0 else "N/A",
        "Gols (média/jogo)": f"{(get_stat('goals', 'total') / total_games):.2f}",
        "Assistências (m/jogo)": f"{(get_stat('goals', 'assists') / total_games):.2f}",
        "Chutes (m/jogo)": f"{(get_stat('shots', 'total') / total_games):.2f}",
        "Chutes no Gol (m/jogo)": f"{(get_stat('shots', 'on') / total_games):.2f}",
        "Passes (m/jogo)": f"{(get_stat('passes', 'total') / total_games):.2f}",
        "Desarmes (m/jogo)": f"{(get_stat('tackles', 'total') / total_games):.2f}",
    }

    recommendations = []
    # heurísticas simples baseadas nas médias
    avg_goals = get_stat('goals', 'total') / total_games
    if avg_goals > 0.35:
        recommendations.append({
            "market": "Jogador para Marcar",
            "recommendation": "Sim",
            "confidence": min(0.95, float(avg_goals) / 0.7),
            "reason": f"Média de {avg_goals:.2f} gols por jogo."
        })

    avg_shots_on = get_stat('shots', 'on') / total_games
    # exemplo para chutes no gol (>0.65 é um bom sinal)
    if avg_shots_on > 0.65:
        recommendations.append({
            "market": "Chutes no Gol",
            "line": "0.5",
            "recommendation": "Acima de",
            "confidence": min(0.95, (avg_shots_on - 0.5) / 1.0),
            "reason": f"Média de {avg_shots_on:.2f} chutes no gol por jogo."
        })

    # ordena por confiança desc
    recommendations = sorted(recommendations, key=lambda x: x.get("confidence", 0), reverse=True)

    return {"key_stats": key_stats, "recommendations": recommendations}

# --- NOVA FUNÇÃO PARA BUSCAR ODDS DE JOGADORES E ENRIQUECER PREDS ---
def find_player_odds(fixture_id: int, player_name: str, predictions: List[Dict]) -> List[Dict]:
    """
    Busca odds do fixture específico e tenta casar mercados de jogador (Anytime goalscorer, player shots, etc).
    Anexa best_odd e bookmaker na prediction caso encontre.
    """
    if not fixture_id or not player_name:
        return predictions

    params = {"fixture": fixture_id}
    try:
        r = requests.get(f"{BASE_URL}/odds", headers=HEADERS, params=params, timeout=20)
        r.raise_for_status()
        odds_data = r.json()
    except Exception:
        return predictions

    if not odds_data or not odds_data.get("response"):
        return predictions

    # prepara busca
    player_parts = [p for p in player_name.lower().split() if p]
    best_odds: Dict[Tuple, Dict] = {}

    for resp in odds_data.get("response", []):
        bookmakers = resp.get("bookmakers", []) or []
        for bookmaker in bookmakers:
            bk_name = bookmaker.get("name")
            for bet in bookmaker.get("bets", []) or []:
                market_name = normalize_str(bet.get("name")).lower()
                for val in bet.get("values", []) or []:
                    value_label = normalize_str(val.get("value")).lower()
                    odd_raw = val.get("odd")
                    odd_val = safe_float(odd_raw, 0.0)

                    # tenta identificar se value refere-se ao jogador
                    # 1) mercados "Anytime Goalscorer" (value costuma ter nome do jogador)
                    match_player_in_value = all(part in value_label for part in player_parts)

                    # mapear possíveis chaves
                    key = None
                    # mercado de goleador qualquer hora
                    if ("anytime" in market_name and "goal" in market_name) or ("goalscorer" in market_name):
                        # se a label contem o nome do jogador -> é candidato
                        if match_player_in_value:
                            # value pode ser "Player Name - Yes" ou só "Player Name"
                            # consideramos "Sim"/"Yes" universalmente como "Sim"
                            # para simplificar, mapeamos para ("Jogador para Marcar", "Sim")
                            if ("yes" in value_label) or ("sim" in value_label) or match_player_in_value:
                                key = ("Jogador para Marcar", "Sim")
                    # mercado de chutes no gol do jogador (nome pode conter "Shots on Target" ou "Player Shots")
                    if key is None and (("player" in market_name and "shot" in market_name) or ("shots on target" in market_name) or ("player shots" in market_name)):
                        # value_label pode ser e.g. "Over 0.5 Player Name" or "Player Name - Over 0.5"
                        if match_player_in_value or ("over 0.5" in value_label and match_player_in_value):
                            # mapeamos para ("Chutes no Gol", "Acima de", "0.5")
                            # some providers use "Over 0.5" or "0.5+" etc. We'll accept '0.5' substring.
                            if "over 0.5" in value_label or "0.5" in value_label:
                                key = ("Chutes no Gol", "Acima de", "0.5")

                    # se encontramos uma key candidata, armazena a melhor odd por key
                    if key:
                        prev = best_odds.get(key)
                        if not prev or odd_val > prev.get("odd", 0):
                            best_odds[key] = {"odd": odd_val, "bookmaker": bk_name, "market_name": bet.get("name")}

    # agora anexa às predictions
    enriched = []
    for p in predictions:
        pkey = (p.get("market"), p.get("recommendation"))
        if "line" in p:
            pkey = (p.get("market"), p.get("recommendation"), p.get("line"))
        best = best_odds.get(pkey)
        if best:
            newp = dict(p)
            newp["best_odd"] = best["odd"]
            newp["bookmaker"] = best["bookmaker"]
            newp["market_name_found"] = best.get("market_name")
            enriched.append(newp)
        else:
            enriched.append(p)
    return enriched

# ----------------- Endpoints da API -----------------
@app.route('/opta/countries', methods=['GET'])
def get_countries():
    try:
        r = requests.get(f"{BASE_URL}/countries", headers=HEADERS, timeout=15)
        r.raise_for_status()
        return jsonify(r.json().get('response', []))
    except Exception as e:
        return jsonify({"error": "Failed to fetch countries", "detail": str(e)}), 502

@app.route('/opta/leagues', methods=['GET'])
def get_leagues():
    country = request.args.get('country')
    country_code = request.args.get('country_code')
    params = {}

    if country:
        params['country'] = country   # nome completo
    elif country_code:
        params['code'] = country_code # código oficial tipo 'BR'

    try:
        r = requests.get(f"{BASE_URL}/leagues", headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        data = r.json().get('response', [])
        leagues = [{"id": item['league']['id'],
                    "name": item['league']['name'],
                    "country": item['country']['name']} for item in data]
        return jsonify(leagues)
    except Exception as e:
        return jsonify({"error": "Failed to fetch leagues", "detail": str(e)}), 502


@app.route('/opta/teams', methods=['GET'])
def get_teams():
    league_id = request.args.get('league_id')
    season = request.args.get('season') or datetime.datetime.now().year
    if not league_id:
        return jsonify({"error": "league_id is required"}), 400
    params = {'league': league_id, 'season': season}
    try:
        r = requests.get(f"{BASE_URL}/teams", headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        data = r.json().get('response', [])
        teams = [{"id": item['team']['id'], "name": item['team']['name']} for item in data]
        return jsonify(teams)
    except Exception as e:
        return jsonify({"error": "Failed to fetch teams", "detail": str(e)}), 502

@app.route('/opta/players', methods=['GET'])
def get_players():
    team_id = request.args.get('team_id')
    season = request.args.get('season') or datetime.datetime.now().year
    if not team_id:
        return jsonify({"error": "team_id is required"}), 400
    params = {'team': team_id, 'season': season}
    try:
        r = requests.get(f"{BASE_URL}/players", headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        data = r.json().get('response', [])
        players = []
        for item in data:
            p = item.get('player', {}) or {}
            players.append({"id": p.get("id"), "name": p.get("name")})
        return jsonify(players)
    except Exception as e:
        return jsonify({"error": "Failed to fetch players", "detail": str(e)}), 502

# --- ENDPOINT DE ANÁLISE (PLAYER) ---
@app.route('/opta/analyze', methods=['GET'])
def analyze_player():
    player_id = request.args.get('player_id')
    season = request.args.get('season') or datetime.datetime.now().year
    if not player_id:
        return jsonify({"error": "player_id is required"}), 400

    try:
        # 1) tenta buscar o jogador por 'id' (algumas versões da API usam 'id' ou 'player')
        params = {'id': player_id, 'season': season}
        r = requests.get(f"{BASE_URL}/players", headers=HEADERS, params=params, timeout=20)
        if r.status_code != 200 or not r.json().get('response'):
            # tenta com 'player' caso 'id' não funcione
            params = {'player': player_id, 'season': season}
            r = requests.get(f"{BASE_URL}/players", headers=HEADERS, params=params, timeout=20)

        r.raise_for_status()
        api_resp = r.json().get('response', [])
        if not api_resp:
            return jsonify({"error": "No player data found"}), 404

        player_data = api_resp[0]  # item com 'player' e 'statistics'
        # monta player_info
        player_info = {
            "name": player_data.get('player', {}).get('name'),
            "age": player_data.get('player', {}).get('age'),
            "photo": player_data.get('player', {}).get('photo'),
            "team": (player_data.get('statistics', [{}])[0].get('team', {}) or {}).get('name'),
            "position": (player_data.get('statistics', [{}])[0].get('games', {}) or {}).get('position')
        }

        # 2) gera recomendações
        analysis_result = process_and_analyze_stats(player_data)

        # 3) busca próximo fixture do time (se houver) para tentar extrair odds
        team_id = (player_data.get('statistics', [{}])[0].get('team', {}) or {}).get('id')
        if team_id:
            try:
                fixtures_resp = requests.get(f"{BASE_URL}/fixtures",
                                             headers=HEADERS,
                                             params={'team': team_id, 'next': 1},
                                             timeout=15)
                fixtures_resp.raise_for_status()
                fixtures_json = fixtures_resp.json().get('response', [])
                if fixtures_json:
                    next_fixture = fixtures_json[0]
                    fixture_id = next_fixture.get('fixture', {}).get('id')
                    # 4) enriquece com odds do fixture (se encontradas)
                    if fixture_id:
                        analysis_result['recommendations'] = find_player_odds(fixture_id, player_info.get('name', ''), analysis_result.get('recommendations', []))
            except Exception:
                # silently ignore odds lookup failure (não queremos quebrar a análise)
                pass

        final_response = {"player_info": player_info, **analysis_result}
        return jsonify(final_response)

    except requests.exceptions.RequestException as e:
        return jsonify({"error": "External API error", "detail": str(e)}), 502
    except Exception as e:
        return jsonify({"error": "Internal server error", "detail": str(e)}), 500

@app.route('/')
def index():
    return "Servidor Opta IA (v5.1) online"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
