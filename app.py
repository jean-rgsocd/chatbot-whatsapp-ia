# app.py — Betting IA Chatbot Backend (independente)
import os
from flask import Flask, request, jsonify
import requests
import radar_ia as radar
import opta_ia as opta

app = Flask(__name__)

API_KEY = os.environ.get("API_SPORTS_KEY")
BASE_URL = "https://v3.football.api-sports.io"

headers = {
    "x-apisports-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

# ✅ Health check
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Betting IA Chatbot ativo 🚀"})


# ✅ Endpoint — Jogos pré-live (do dia atual)
@app.route("/pre-live-games", methods=["GET"])
def pre_live_games():
    try:
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")
        url = f"{BASE_URL}/fixtures?date={today}"
        res = requests.get(url, headers=headers)
        data = res.json()

        games = []
        for fixture in data.get("response", []):
            game_id = fixture["fixture"]["id"]
            label = f"{fixture['teams']['home']['name']} vs {fixture['teams']['away']['name']}"
            games.append({"game_id": game_id, "label": label})

        return jsonify(games), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ✅ Endpoint — Jogos ao vivo
@app.route("/live-games", methods=["GET"])
def live_games():
    try:
        url = f"{BASE_URL}/fixtures?live=all"
        res = requests.get(url, headers=headers)
        data = res.json()

        games = []
        for fixture in data.get("response", []):
            game_id = fixture["fixture"]["id"]
            label = f"{fixture['teams']['home']['name']} vs {fixture['teams']['away']['name']}"
            games.append({"game_id": game_id, "label": label})

        return jsonify(games), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ✅ Endpoint — Analisar jogo pré-live
@app.route("/analyze-game", methods=["POST"])
def analyze_game():
    try:
        data = request.get_json()
        game_id = data.get("game_id")
        if not game_id:
            return jsonify({"error": "game_id é obrigatório"}), 400

        # Por enquanto, análise básica (pode integrar com lógica própria)
        analysis_text = f"📊 Análise pré-live do jogo {game_id}\n\n(Aqui entrariam dados do Tipster/estatísticas)."

        return jsonify({"analysis_text": analysis_text}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ✅ Endpoint — Analisar jogo ao vivo
@app.route("/analyze-live-game", methods=["POST"])
def analyze_live_game():
    try:
        data = request.get_json()
        game_id = data.get("game_id")
        if not game_id:
            return jsonify({"error": "game_id é obrigatório"}), 400

        radar_analysis = radar.stats_aovivo(game_id)
        analysis_text = f"⚡ Análise ao vivo do jogo {game_id}\n\n{radar_analysis}"

        return jsonify({"analysis_text": analysis_text}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ✅ Endpoint — Estatísticas de jogador (Opta)
@app.route("/opta-player", methods=["POST"])
def opta_player():
    try:
        data = request.get_json()
        player_id = data.get("player_id")
        if not player_id:
            return jsonify({"error": "player_id é obrigatório"}), 400

        analysis = opta.analyze_player_stats(player_id)
        return jsonify({"opta": analysis}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 🚀 Start local
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
