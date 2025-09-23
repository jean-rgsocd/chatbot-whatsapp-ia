# app.py — API completa (pré-jogo, análise e ao-vivo)
import os
import traceback
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# --- Importa módulos de análise (se existirem) ---
try:
    import sports_betting_analyzer as sba
except Exception:
    sba = None

try:
    import radar_ia as radar
except Exception:
    radar = None

try:
    import opta_ia as opta
except Exception:
    opta = None

# --- Config ---
PRINCIPAL_LEAGUE_IDS = {39, 140, 135, 78, 61, 2, 3, 71, 253, 281, 88, 94}
API_SPORTS_KEY = os.environ.get("API_SPORTS_KEY")

# --- Formatadores de texto de resposta (prontos para WhatsApp) ---
def format_full_pre_game_analysis(game_analysis: dict, players_analysis: list) -> str:
    if not game_analysis or 'raw_fixture' not in game_analysis:
        return "❌ Não foi possível obter a análise para este jogo."
    fixture = game_analysis.get('raw_fixture', {})
    home_team = fixture.get('teams', {}).get('home', {}).get('name', 'Casa')
    away_team = fixture.get('teams', {}).get('away', {}).get('name', 'Visitante')
    top3 = game_analysis.get('top3', [])

    lines = [f"*Análise Completa*: {home_team} x {away_team}"]
    lines.append("\n🤖 *TipsterIA — Dicas principais*")
    if not top3:
        lines.append("_Nenhuma recomendação principal encontrada._")
    else:
        for pick in top3:
            market = pick.get('market') or pick.get('name') or "Mercado"
            rec = pick.get('recommendation') or pick.get('prediction') or "N/A"
            conf = pick.get('confidence')
            conf_text = f" (confiança {conf:.2f})" if isinstance(conf, (float, int)) else ""
            lines.append(f"- *{market}*: {rec}{conf_text}")

    lines.append("\n👤 *OptaIA — Jogadores em destaque*")
    if not players_analysis:
        lines.append("_Nenhuma análise de jogador disponível._")
    else:
        for p in players_analysis:
            if not p or not p.get('player_info'): continue
            pi = p['player_info']
            lines.append(f"\n*{pi.get('name')}* — {pi.get('team')}")
            recs = p.get('recommendations') or []
            if not recs:
                lines.append("  - Sem dicas específicas.")
            else:
                for r in recs:
                    lines.append(f"  - *{r.get('market')}*: {r.get('recommendation')}")

    lines.append("\n_Lembre-se: use essa análise como apoio, não como garantia._")
    return "\n".join(lines)


def format_live_analysis(radar_data: dict, live_tips: list) -> str:
    if not radar_data or 'fixture' not in radar_data:
        return "❌ Não foi possível obter os dados ao vivo para este jogo."
    teams = radar_data.get('teams', {})
    score = radar_data.get('score', {}) or {}
    status = radar_data.get('status', {}) or {}
    stats = radar_data.get('statistics', {}) or {}

    home_team = teams.get('home', {}).get('name', 'Casa')
    away_team = teams.get('away', {}).get('name', 'Visitante')
    home_score = score.get('home', 0)
    away_score = score.get('away', 0)
    elapsed = status.get('elapsed', '?')

    lines = [f"*Análise Ao Vivo*: {home_team} {home_score} x {away_score} {away_team} ({elapsed}')"]
    lines.append("\n📡 *RadarIA — Estatísticas rápidas*")
    home_stats = stats.get('home', {})
    away_stats = stats.get('away', {})
    lines.append(f"- Posse: {home_stats.get('ball_possession', '0%')} / {away_stats.get('ball_possession', '0%')}")
    lines.append(f"- Chutes (total): {home_stats.get('total_shots', 0)} / {away_stats.get('total_shots', 0)}")
    lines.append(f"- Escanteios: {home_stats.get('corner_kicks', 0)} / {away_stats.get('corner_kicks', 0)}")

    lines.append("\n💡 *Dicas em tempo real (TipsterIA)*")
    if not live_tips:
        lines.append("_Nenhuma oportunidade clara no momento._")
    else:
        for tip in live_tips:
            market = tip.get('market', 'Mercado')
            rec = tip.get('recommendation', '')
            reason = tip.get('reason', '')
            conf = tip.get('confidence')
            conf_text = f" (conf {conf:.2f})" if isinstance(conf, (float, int)) else ""
            lines.append(f"- *{market}*: {rec}{conf_text} — _{reason}_")
    return "\n".join(lines)


# --- Helpers ---
def module_check():
    missing = []
    if not sba: missing.append("sports_betting_analyzer")
    if not radar: missing.append("radar_ia")
    if not opta: missing.append("opta_ia")
    if not API_SPORTS_KEY:
        missing.append("API_SPORTS_KEY (env)")
    return missing


# --- Endpoints --- #
@app.route("/", methods=["GET"])
def health_check():
    return jsonify({
        "status": "ok",
        "app": "chatbot-whatsapp-ia",
        "modules": {
            "sports_betting_analyzer": bool(sba),
            "radar_ia": bool(radar),
            "opta_ia": bool(opta),
        },
        "env_api_sports_key_set": bool(API_SPORTS_KEY)
    })


@app.route("/pre-live-games", methods=["GET"])
def get_pre_live_games():
    if not sba:
        return jsonify({"error": "Módulo sports_betting_analyzer não disponível"}), 500
    try:
        # days_forward=0 busca jogos de hoje; ajuste se quiser mais dias
        all_games = sba.get_fixtures_for_dates(days_forward=0)
        pre_games = [g for g in all_games if g.get('league', {}).get('id') in PRINCIPAL_LEAGUE_IDS and g.get('type') == 'scheduled']
        formatted = [{"game_id": g.get("game_id"), "label": f"{g.get('teams', {}).get('home', {}).get('name', 'A')} x {g.get('teams', {}).get('away', {}).get('name', 'B')}"} for g in pre_games[:30]]
        return jsonify(formatted)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "Erro interno ao listar jogos", "detail": str(e)}), 500


@app.route("/analyze-game", methods=["POST"])
def analyze_game():
    if not sba:
        return jsonify({"error": "Módulo sports_betting_analyzer não disponível"}), 500
    data = request.get_json(silent=True) or {}
    game_id = data.get("game_id") or request.args.get("game_id")
    if not game_id:
        return jsonify({"error": "game_id é obrigatório"}), 400
    try:
        # análise pré-jogo
        game_analysis = sba.analyze(game_id=int(game_id))
        players_analysis = []
        if game_analysis and opta:
            fixture = game_analysis.get('raw_fixture', {})
            team_ids = [fixture.get('teams', {}).get(side, {}).get('id') for side in ['home', 'away']]
            for team_id in team_ids:
                if team_id:
                    players = opta.get_players_for_team(team_id=team_id)
                    if players:
                        # Analisa o primeiro jogador para economizar chamada
                        pa = opta.analyze_player(player_id=players[0]['id'])
                        if pa:
                            players_analysis.append(pa)
        text = format_full_pre_game_analysis(game_analysis, players_analysis)
        return jsonify({"analysis_text": text})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "Erro ao analisar jogo (pré-jogo)", "detail": str(e)}), 500


@app.route("/analyze-live-game", methods=["POST"])
def analyze_live_game():
    """
    Recebe { "game_id": <id> } e devolve texto com análise ao vivo:
    - coleta dados via radar_ia.stats_aovivo(game_id)
    - roda heurísticas ao vivo do sports_betting_analyzer.analyze_live_from_stats(radar_data)
    - formata com format_live_analysis
    """
    missing = module_check()
    if missing:
        return jsonify({"error": "Módulos/variáveis ausentes", "missing": missing}), 500

    data = request.get_json(silent=True) or {}
    game_id = data.get("game_id") or request.args.get("game_id")
    if not game_id:
        return jsonify({"error": "game_id é obrigatório"}), 400
    try:
        # coleta dados ao vivo
        radar_data = radar.stats_aovivo(int(game_id))
        if radar_data is None:
            return jsonify({"error": "Dados ao vivo não encontrados para esse game_id"}), 404

        # gera dicas ao vivo baseadas nas stats
        live_tips = []
        if sba and hasattr(sba, "analyze_live_from_stats"):
            try:
                live_tips = sba.analyze_live_from_stats(radar_data) or []
            except Exception:
                traceback.print_exc()
                live_tips = []

        text = format_live_analysis(radar_data, live_tips)
        return jsonify({"analysis_text": text, "raw_tips": live_tips})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "Erro ao analisar jogo ao vivo", "detail": str(e)}), 500


# --- Run ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    # Em produção, o Render usa gunicorn; esse run é para testes locais.
    app.run(host="0.0.0.0", port=port)
