# app.py (VERSÃO REFINADA COM FILTRO DE LIGAS E MELHOR ANÁLISE)
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

# ATUALIZAÇÃO: IDs das principais ligas para filtrar
PRINCIPAL_LEAGUE_IDS = {
    39, # Premier League (Inglaterra)
    140, # La Liga (Espanha)
    135, # Serie A (Itália)
    78, # Bundesliga (Alemanha)
    61, # Ligue 1 (França)
    2, # Champions League (Europa)
    3, # Europa League (Europa)
    71, # Brasileirão Série A (Brasil)
    253, # MLS (EUA)
    281, # Saudi League (Arábia Saudita)
    88, # Eredivisie (Holanda)
    94, # Primeira Liga (Portugal)
}

# Dicionário para gerenciar o estado da conversa de cada usuário
USER_STATE = {}

# (As funções de formatação de análise permanecem as mesmas)
def format_full_pre_game_analysis(game_analysis: dict, players_analysis: list) -> str:
    if not game_analysis or 'summary' not in game_analysis:
        return "Não foi possível obter a análise para este jogo."
    summary = game_analysis.get('summary', {}); top3 = game_analysis.get('top3', [])
    home_team = summary.get('home_team', 'Casa'); away_team = summary.get('away_team', 'Visitante')
    lines = [f"Análise Completa: *{home_team} vs {away_team}*"]
    lines.append("\n*🤖 Análise da Partida (TipsterIA)*")
    for pick in top3:
        market = pick.get('market_name_found', pick.get('market', '')).replace('_', ' ').title()
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
                if not recs: lines.append("  - Sem dicas de aposta específicas.")
                else:
                    for rec in recs: lines.append(f"  - *{rec.get('market')}*: {rec.get('recommendation')}")
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


def get_greeting():
    current_hour = datetime.now().hour
    if 5 <= current_hour < 12: return "Bom dia"
    elif 12 <= current_hour < 18: return "Boa tarde"
    else: return "Boa noite"

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        incoming_msg = request.form.get("Body", "").strip().lower()
        from_number = request.form.get("From", "unknown")
        resp = MessagingResponse()
        msg = resp.message()

        if from_number not in USER_STATE:
            USER_STATE[from_number] = {'step': 'welcome'}
        user_step = USER_STATE[from_number].get('step')

        # --- LÓGICA DE ESCOLHA DE JOGO (INPUT NUMÉRICO) ---
        if incoming_msg.isdigit():
            idx = int(incoming_msg) - 1
            game_list_type = ""
            if user_step == 'awaiting_pre_game_choice': game_list_type = 'pre_games'
            elif user_step == 'awaiting_live_game_choice': game_list_type = 'live_games'

            if game_list_type:
                games = USER_STATE[from_number].get(game_list_type, [])
                if 0 <= idx < len(games):
                    game = games[idx]
                    game_id = game.get('game_id')
                    
                    if game_list_type == 'pre_games':
                        # Lógica de análise pré-jogo completa
                        game_analysis = sba.analyze(game_id=game_id) if sba else None
                        players_analysis = []
                        if opta:
                            # ATUALIZAÇÃO: Analisa um jogador de cada time
                            home_team_id = game.get('teams', {}).get('home', {}).get('id')
                            away_team_id = game.get('teams', {}).get('away', {}).get('id')
                            for team_id in [home_team_id, away_team_id]:
                                players = opta.get_players_for_team(team_id=team_id)
                                if players:
                                    analysis = opta.analyze_player(player_id=players[0]['id'])
                                    if analysis: players_analysis.append(analysis)
                        response_text = format_full_pre_game_analysis(game_analysis, players_analysis)
                    else: # live_games
                        # Lógica de análise ao vivo completa
                        radar_analysis = radar.stats_aovivo(game_id=game_id) if radar else None
                        live_tips = sba.analyze_live_from_stats(radar_analysis) if sba and radar_analysis else []
                        response_text = format_live_analysis(radar_analysis, live_tips)
                    
                    msg.body(response_text)
                    USER_STATE[from_number]['step'] = 'welcome' # Reseta o estado
                else:
                    msg.body("Número inválido. Por favor, escolha um número da lista.")
            else:
                # Se o usuário enviar um número sem um contexto, mostre o menu principal
                msg.body(f"{get_greeting()}! Bem-vindo(a) ao Betting IA.\n\nEscolha uma opção:\n1. Jogos Pré-Live\n2. Jogos Ao Vivo")
                USER_STATE[from_number]['step'] = 'awaiting_menu_choice'
        
        # --- LÓGICA DE NAVEGAÇÃO NO MENU (INPUT DE TEXTO) ---
        else:
            if "pré" in incoming_msg or "pre" in incoming_msg or "1" == incoming_msg:
                all_games = sba.get_fixtures_for_dates(days_forward=0) if sba else []
                # ATUALIZAÇÃO: Filtra apenas jogos de ligas principais e que ainda não começaram
                pre_games = [g for g in all_games if g['league'].get('id') in PRINCIPAL_LEAGUE_IDS and g['type'] == 'scheduled']
                if not pre_games:
                    msg.body("Nenhum jogo pré-live das principais ligas encontrado para hoje.")
                else:
                    USER_STATE[from_number]['step'] = 'awaiting_pre_game_choice'
                    USER_STATE[from_number]['pre_games'] = pre_games
                    lines = ["*Jogos Pré-Live (Principais Ligas):*\n"]
                    for i, game in enumerate(pre_games[:20], start=1):
                        home = game.get('teams', {}).get('home', {}).get('name', 'Time A')
                        away = game.get('teams', {}).get('away', {}).get('name', 'Time B')
                        lines.append(f"{i}. {home} x {away}")
                    lines.append("\nDigite o número do jogo para receber a análise completa.")
                    msg.body("\n".join(lines))

            elif "vivo" in incoming_msg or "2" == incoming_msg:
                all_games = sba.get_fixtures_for_dates(days_forward=0) if sba else []
                # ATUALIZAÇÃO: Filtra apenas jogos de ligas principais que estão ao vivo
                live_games = [g for g in all_games if g['league'].get('id') in PRINCIPAL_LEAGUE_IDS and g['type'] == 'live']
                if not live_games:
                    msg.body("Nenhum jogo das principais ligas acontecendo no momento.")
                else:
                    USER_STATE[from_number]['step'] = 'awaiting_live_game_choice'
                    USER_STATE[from_number]['live_games'] = live_games
                    lines = ["*Jogos Acontecendo Agora (Principais Ligas):*\n"]
                    for i, game in enumerate(live_games[:20], start=1):
                        home = game.get('teams', {}).get('home', {}).get('name', 'Time A')
                        away = game.get('teams', {}).get('away', {}).get('name', 'Time B')
                        lines.append(f"{i}. {home} x {away}")
                    lines.append("\nDigite o número do jogo para receber a análise ao vivo.")
                    msg.body("\n".join(lines))
            
            else:
                # Qualquer outra mensagem mostra o menu principal
                msg.body(f"{get_greeting()}! Bem-vindo(a) ao Betting IA.\n\nEscolha uma opção:\n1. Jogos Pré-Live\n2. Jogos Ao Vivo")
                USER_STATE[from_number]['step'] = 'awaiting_menu_choice'

        return str(resp)

    except Exception:
        traceback.print_exc()
        resp = MessagingResponse()
        resp.message("Ocorreu um erro interno. A equipe já foi notificada.")
        return str(resp), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=True)
