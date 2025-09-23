# tipster.py — Betting IA (unificado: Tipster, Radar, Opta, Flask)
import os
import requests
from datetime import date
from flask import Flask, request, jsonify

# =========================
# 🔑 Configuração API-FOOTBALL
# =========================
API_KEY = os.environ.get("API_SPORTS_KEY")
if not API_KEY:
    raise ValueError("❌ API_SPORTS_KEY não configurada nas variáveis de ambiente!")

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {
    "x-apisports-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

app = Flask(__name__)

# =========================
# 📊 SPORTS BETTING ANALYZER (pré-live)
# =========================
def get_fixtures_by_date(target_date=None):
    """Retorna jogos do dia específico"""
    if not target_date:
        target_date = date.today().strftime("%Y-%m-%d")

    url = f"{BASE_URL}/fixtures?date={target_date}"
    res = requests.get(url, headers=HEADERS)
    data = res.json()
    return data.get("response", [])


def analyze_game(game_id):
    """Análise pré-live básica (pode expandir)"""
    url = f"{BASE_URL}/fixtures?id={game_id}"
    res = requests.get(url, headers=HEADERS)
    data = res.json()

    if not data.get("response"):
        return f"❌ Nenhum dado encontrado para o jogo {game_id}"

    fixture = data["response"][0]
    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]

    # Tipster Inteligente (exemplo simples, pode expandir tuas regras aqui)
    analysis = f"📊 Jogo {home} vs {away}\n"
    analysis += f"Data: {fixture['fixture']['date']}\n"
    analysis += "⚖️ Tipster: tendência equilibrada (pré-live)\n"

    return analysis


# =========================
# ⚡ RADAR IA (ao vivo)
# =========================
def analyze_live_game(game_id):
    """Busca estatísticas ao vivo de um jogo"""
    url = f"{BASE_URL}/fixtures?id={game_id}&live=all"
    res = requests.get(url, headers=HEADERS)
    data = res.json()

    if not data.get("response"):
        return f"❌ Nenhum jogo ao vivo encontrado para ID {game_id}"

    fixture = data["response"][0]
    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]
    goals_home = fixture["goals"]["home"]
    goals_away = fixture["goals"]["away"]

    analysis = f"⚡ AO VIVO: {home} {goals_home} x {goals_away} {away}\n"
    analysis += f"Status: {fixture['fixture']['status']['long']}\n"
    analysis += "📡 Radar IA: pressão ofensiva detectada.\n"

    return analysis


def get_live_fixtures():
    """Lista todos os jogos ao vivo"""
    url = f"{BASE_URL}/fixtures?live=all"
    res = requests.get(url, headers=HEADERS)
    data = res.json()
    return data.get("response", [])


# =========================
# 👤 OPTA IA (estatísticas jogador)
# =========================
def analyze_player_stats(player_id, season=2025):
    url = f"{BASE_URL}/players?id={player_id}&season={season}"
    res = requests.get(url, headers=HEADERS)
    data = res.json()

    if not data.get("response"):
        return f"❌ Nenhum dado encontrado para jogador {player_id}"

    player = data["response"][0]["player"]
    stats = data["response"][0]["statistics"][0]

    summary = (
        f"👤 {player['name']} ({player['nationality']})\n"
        f"Idade: {player['age']}\n"
        f"Time: {stats['team']['name']}\n"
        f"Liga: {stats['league']['name']}\n\n"
        f"⚽ Gols: {stats['goals']['total']}\n"
        f"🅰️ Assistências: {stats['goals']['assists']}\n"
        f"🎯 Chutes no alvo: {stats['shots']['on']}\n"
        f"📈 Partidas: {stats['games']['appearences']}"
    )
    return summary


# =========================
# 🌐 ENDPOINTS FLASK
# =========================
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Betting IA Tipster ativo 🚀"})


@app.route("/fixtures", methods=["GET"])
def fixtures():
    """Retorna fixtures do dia ou data específica"""
    date_param = request.args.get("date")
    try:
        fixtures = get_fixtures_by_date(date_param)
        games = []
        for f in fixtures:
            games.append({
                "game_id": f["fixture"]["id"],
                "label": f"{f['teams']['home']['name']} vs {f['teams']['away']['name']}"
            })
        return jsonify(games), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/fixtures/live", methods=["GET"])
def fixtures_live():
    """Retorna jogos ao vivo"""
    try:
        fixtures = get_live_fixtures()
        games = []
        for f in fixtures:
            games.append({
                "game_id": f["fixture"]["id"],
                "label": f"{f['teams']['home']['name']} vs {f['teams']['away']['name']}"
            })
        return jsonify(games), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/analyze/game", methods=["POST"])
def api_analyze_game():
    """Análise pré-live"""
    try:
        data = request.get_json()
        game_id = data.get("game_id")
        if not game_id:
            return jsonify({"error": "game_id é obrigatório"}), 400

        result = analyze_game(game_id)
        return jsonify({"analysis_text": result}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/analyze/live", methods=["POST"])
def api_analyze_live():
    """Análise ao vivo"""
    try:
        data = request.get_json()
        game_id = data.get("game_id")
        if not game_id:
            return jsonify({"error": "game_id é obrigatório"}), 400

        result = analyze_live_game(game_id)
        return jsonify({"analysis_text": result}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/players", methods=["GET"])
def api_players():
    """Estatísticas de jogador"""
    try:
        player_id = request.args.get("id")
        if not player_id:
            return jsonify({"error": "id do jogador é obrigatório"}), 400

        result = analyze_player_stats(player_id)
        return jsonify({"opta": result}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# 🚀 MAIN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
