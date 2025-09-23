# app.py (Versão final como API de Análise)
import os
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# Importa todos os seus módulos de análise
try:
    import sports_betting_analyzer as sba
    import radar_ia as radar
    import opta_ia as opta
except ImportError:
    sba, radar, opta = None, None, None

load_dotenv()
app = Flask(__name__)

# --- CONFIGURAÇÃO ---
PRINCIPAL_LEAGUE_IDS = {39, 140, 135, 78, 61, 2, 3, 71, 253, 281, 88, 94}

# --- SUAS FUNÇÕES DE FORMATAÇÃO (VERSÕES COMPLETAS E ORIGINAIS) ---

def format_full_pre_game_analysis(game_analysis: dict, players_analysis: list) -> str:
    if not game_analysis or 'raw_fixture' not in game_analysis:
        return "Não foi possível obter a análise para este jogo."
    
    fixture = game_analysis.get('raw_fixture', {})
    home_team = fixture.get('teams', {}).get('home', {}).get('name', 'Casa')
    away_team = fixture.get('teams', {}).get('away', {}).get('name', 'Visitante')
    top3 = game_analysis.get('top3', [])

    lines = [f"Análise Completa: *{home_team} vs {away_team}*"]
    lines.append("\n*🤖 Análise da Partida (TipsterIA)*")
    if not top3:
        lines.append("_Nenhuma dica principal encontrada._")
    else:
        for pick in top3:
            line = f"- *{pick.get('market')}*: {pick.get('recommendation', 'N/A')}"
            lines.append(line)
            
    lines.append("\n*👤 Jogadores em Destaque (OptaIA)*")
    if not players_analysis:
        lines.append("_Nenhuma análise de jogador disponível._")
    else:
        for player_result in players_analysis:
            if player_result and player_result.get('player_info'):
                p_info = player_result['player_info']
                recs = player_result.get('recommendations', [])
                lines.append(f"\n*{p_info.get('name')}* ({p_info.get('team')})")
                if not recs:
                    lines.append("  - Sem dicas de aposta específicas.")
                else:
                    for rec in recs:
                        lines.append(f"  - *{rec.get('market')}*: {rec.get('recommendation')}")
                        
    lines.append("\n_Lembre-se: analise por conta própria._")
    return "\n".join(lines)

def format_live_analysis(radar_data: dict, live_tips: list) -> str:
    if not radar_data or 'fixture' not in radar_data:
        return "Não foi possível obter os dados ao vivo para este jogo."
    teams = radar_data.get('teams', {}); score = radar_data.get('score', {}).get('fulltime', {})
    status = radar_data.get('status', {}); stats = radar_data.get('statistics', {})
    home_stats = stats.get('home', {}); away_stats = stats.get('away', {})
    home_team = teams.get('home', {}).get('name', 'Casa'); away_team = teams.get('away', {}).get('name', 'Visitante')
    home_score = score.get('home', 0); away_score = score.get('away', 0); elapsed = status.get('elapsed', '?')
    lines = [f"Análise Ao Vivo: *{home_team} {home_score} x {away_score} {away_team}* ({elapsed}')"]
    lines.append("\n*📡 RadarIA - Estatísticas Atuais*")
    lines.append(f"Posse: *{home_stats.get('ball_possession', '0%')}* / *{away_stats.get('ball_possession', '0%')}*")
    lines.append(f"Chutes: *{home_stats.get('total_shots', 0)}* / *{away_stats.get('total_shots', 0)}*")
    lines.append("\n*💡 TipsterIA - Dicas Baseadas no Jogo*")
    if not live_tips: lines.append("_Nenhuma oportunidade clara no momento._")
    else:
        for tip in live_tips: lines.append(f"- *{tip.get('market')}*: {tip.get('recommendation')} (_{tip.get('reason')}_)")
    return "\n".join(lines)


# --- ENDPOINTS DA API ---

@app.route('/pre-live-games', methods=['GET'])
def get_pre_live_games():
    if not sba: return jsonify({"error": "Módulo sba não disponível"}), 500
    all_games = sba.get_fixtures_for_dates(days_forward=0)
    pre_games = [g for g in all_games if g.get('league', {}).get('id') in PRINCIPAL_LEAGUE_IDS and g['type'] == 'scheduled']
    formatted_games = [{
        "game_id": g.get("game_id"),
        "label": f"{g.get('teams', {}).get('home', {}).get('name', 'A')} x {g.get('teams', {}).get('away', {}).get('name', 'B')}"
    } for g in pre_games[:20]]
    return jsonify(formatted_games)

@app.route('/analyze-game', methods=['POST'])
def analyze_game():
    data = request.get_json(); game_id = data.get('game_id')
    if not game_id: return jsonify({"error": "game_id é obrigatório"}), 400
    if not sba or not opta: return jsonify({"error": "Módulos de análise não disponíveis"}), 500

    try:
        game_analysis = sba.analyze(game_id=int(game_id))
        players_analysis = []
        if game_analysis and opta:
            fixture = game_analysis.get('raw_fixture', {})
            team_ids = [fixture.get('teams', {}).get(side, {}).get('id') for side in ['home', 'away']]
            for team_id in team_ids:
                if team_id:
                    players = opta.get_players_for_team(team_id=team_id)
                    if players: # Analisa apenas o primeiro jogador de cada time para ser rápido
                        analysis = opta.analyze_player(player_id=players[0]['id'])
                        if analysis: players_analysis.append(analysis)
        
        response_text = format_full_pre_game_analysis(game_analysis, players_analysis)
        return jsonify({"analysis_text": response_text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/')
def health_check():
    return "API de Análise de Apostas está online."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

