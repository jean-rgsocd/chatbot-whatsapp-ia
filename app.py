# app.py (VERSÃO FINAL COM ANÁLISE AO VIVO INTELIGENTE)
import os
import traceback
from flask import Flask, request
from dotenv import load_dotenv
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime

# Carrega variáveis de ambiente
load_dotenv()

# Tenta importar os módulos de análise
try:
    import sports_betting_analyzer as sba
except ImportError: sba = None
try:
    import radar_ia as radar
except ImportError: radar = None
try:
    import opta_ia as opta
except ImportError: opta = None

app = Flask(__name__)
LAST_GAMES = {}

def format_full_pre_game_analysis(game_analysis: dict, players_analysis: list) -> str:
    # (Esta função permanece a mesma da etapa anterior)
    if not game_analysis or 'summary' not in game_analysis:
        return "Não foi possível obter a análise para este jogo."
    summary = game_analysis.get('summary', {})
    top3 = game_analysis.get('top3', [])
    home_team = summary.get('home_team', 'Casa'); away_team = summary.get('away_team', 'Visitante')
    lines = [f"Análise Completa: *{home_team} vs {away_team}*"]
    lines.append("\n*🤖 Análise da Partida (TipsterIA)*")
    for pick in top3:
        market = pick.get('market_name_found', pick.get('market', '')).replace('_', ' ').title()
        rec = pick.get('recommendation', 'N/A')
        conf = pick.get('confidence', 0)
        odd = pick.get('best_odd', None)
        bookmaker = pick.get('bookmaker', None)
        line = f"- *{market}*: {rec} (Confiança: {conf:.0%})"
        if odd and bookmaker: line += f" - Odd *{odd}* na {bookmaker.title()}"
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
                if not recs: lines.append("  - Sem dicas de aposta específicas.")
                else:
                    for rec in recs: lines.append(f"  - *{rec.get('market')}*: {rec.get('recommendation')}")
    lines.append("\n_Lembre-se: analise por conta própria._")
    return "\n".join(lines)


def format_live_analysis(radar_data: dict, live_tips: list) -> str:
    """NOVA VERSÃO: Formata os dados do Radar e as dicas do Tipster."""
    if not radar_data or 'fixture' not in radar_data:
        return "Não foi possível obter os dados ao vivo para este jogo."

    teams = radar_data.get('teams', {})
    score = radar_data.get('score', {}).get('fulltime', {})
    status = radar_data.get('status', {})
    stats = radar_data.get('statistics', {})
    home_stats = stats.get('home', {})
    away_stats = stats.get('away', {})

    home_team = teams.get('home', {}).get('name', 'Casa')
    away_team = teams.get('away', {}).get('name', 'Visitante')
    home_score = score.get('home', 0)
    away_score = score.get('away', 0)
    elapsed = status.get('elapsed', '?')

    lines = [f"Análise Ao Vivo: *{home_team} {home_score} x {away_score} {away_team}* ({elapsed}')"]
    
    # Parte 1: Dados do Radar
    lines.append("\n*📡 RadarIA - Estatísticas Atuais*")
    lines.append(f"Posse: *{home_stats.get('ball_possession', '0%')}* / *{away_stats.get('ball_possession', '0%')}*")
    lines.append(f"Chutes: *{home_stats.get('total_shots', 0)}* / *{away_stats.get('total_shots', 0)}*")
    lines.append(f"Escanteios: *{home_stats.get('corner_kicks', 0)}* / *{away_stats.get('corner_kicks', 0)}*")
    
    # Parte 2: Dicas Inteligentes do Tipster
    lines.append("\n*💡 TipsterIA - Dicas Baseadas no Jogo*")
    if not live_tips:
        lines.append("_Nenhuma oportunidade clara no momento. Aguardando..._")
    else:
        for tip in live_tips:
            lines.append(f"- *{tip.get('market')}*: {tip.get('recommendation')} (_{tip.get('reason')}_)")
            
    return "\n".join(lines)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        incoming_msg = request.form.get("Body", "").strip().lower()
        from_number = request.form.get("From", "unknown")
        resp = MessagingResponse()
        msg = resp.message()

        if incoming_msg.startswith("jogos"):
            games = sba.get_fixtures_for_dates(days_forward=0) if sba else []
            if not games:
                msg.body("Nenhum jogo encontrado para hoje."); return str(resp)
            LAST_GAMES[from_number] = games
            lines = ["*Jogos de hoje:*"]
            for i, game in enumerate(games[:20], start=1):
                home = game.get('teams', {}).get('home', {}).get('name', 'Time A')
                away = game.get('teams', {}).get('away', {}).get('name', 'Time B')
                lines.append(f"{i}. {home} x {away}")
            lines.append("\nResponda com 'analisar <n>' ou 'ao vivo <n>'")
            msg.body("\n".join(lines)); return str(resp)

        if incoming_msg.startswith("analisar"):
            parts = incoming_msg.split(); idx = int(parts[1]) - 1
            games = LAST_GAMES.get(from_number, [])
            if not (0 <= idx < len(games)):
                msg.body("Índice inválido."); return str(resp)
            game = games[idx]
            game_id = game.get('game_id')
            game_analysis = sba.analyze(game_id=game_id) if sba else None
            players_analysis = []
            if opta:
                home_team_id = game.get('teams', {}).get('home', {}).get('id')
                players = opta.get_players_for_team(team_id=home_team_id)
                if players:
                    analysis = opta.analyze_player(player_id=players[0]['id'])
                    if analysis: players_analysis.append(analysis)
            formatted_response = format_full_pre_game_analysis(game_analysis, players_analysis)
            msg.body(formatted_response); return str(resp)

        if incoming_msg.startswith("ao vivo"):
            parts = incoming_msg.split(); idx = int(parts[1]) - 1
            games = LAST_GAMES.get(from_number, [])
            if not (0 <= idx < len(games)):
                msg.body("Índice inválido."); return str(resp)
            game = games[idx]
            game_id = game.get('game_id')
            
            # 1. Busca dados do Radar
            radar_analysis = radar.stats_aovivo(game_id=game_id) if radar else None
            
            # 2. Gera dicas com o Tipster usando os dados do Radar
            live_tips = sba.analyze_live_from_stats(radar_analysis) if sba and radar_analysis else []
            
            # 3. Formata e envia a resposta completa
            formatted_response = format_live_analysis(radar_analysis, live_tips)
            msg.body(formatted_response); return str(resp)

        msg.body("Olá! Envie 'jogos hoje' para ver a lista de partidas."); return str(resp)
    except Exception:
        traceback.print_exc()
        resp = MessagingResponse()
        resp.message("Ocorreu um erro interno. A equipe já foi notificada.")
        return str(resp), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=True)
