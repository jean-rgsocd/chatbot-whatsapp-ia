# app.py — Betting IA Chatbot Backend (independente)
import os
from flask import Flask, request, jsonify
import sports_betting_analyzer as sba
import radar_ia as radar
import opta_ia as opta

app = Flask(__name__)

# ✅ Health check
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Betting IA Chatbot ativo 🚀"})


# ✅ Endpoint — Jogos pré-live
@app.route("/pre-live-games", methods=["GET"])
def pre_live_games():
    try:
        games = sba.get_pre_live_games()
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

        # Tipster
        tipster_analysis = sba.analyze_game(game_id)
        # Opta
        opta_analysis = opta.analyze_player_stats(game_id)

        response = {
            "tipster": tipster_analysis,
            "opta": opta_analysis,
            "analysis_text": f"📊 Análise do jogo {game_id}\n\n"
                             f"Tipster: {tipster_analysis}\n\n"
                             f"Opta: {opta_analysis}"
        }
        return jsonify(response), 200

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

        response = {
            "radar": radar_analysis,
            "analysis_text": f"⚡ Análise ao vivo do jogo {game_id}\n\n{radar_analysis}"
        }
        return jsonify(response), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ✅ Endpoint — Estatísticas Opta (exemplo separado)
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
