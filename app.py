# app.py (VERSÃO FINAL E COMPLETA PARA META API)
import os
import traceback
import requests
from flask import Flask, request
from dotenv import load_dotenv
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

# --- VARIÁVEIS DE AMBIENTE PARA A META ---
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")
META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN")
META_PHONE_NUMBER_ID = os.environ.get("META_PHONE_NUMBER_ID")

# IDs das principais ligas para filtrar
PRINCIPAL_LEAGUE_IDS = {
    39, 140, 135, 78, 61, 2, 3, 71, 253, 281, 88, 94,
}

# Dicionário para gerenciar o estado da conversa de cada usuário
USER_STATE = {}

def format_full_pre_game_analysis(game_analysis: dict, players_analysis: list) -> str:
    if not game_analysis or 'summary' not in game_analysis:
        return "Não foi possível obter a análise para este jogo."
    summary = game_analysis.get('summary', {}); top3 = game_analysis.get('top3', [])
    home_team = summary.get('home_team', 'Casa'); away_team = summary.get('away_team', 'Visitante')
    lines = [f"Análise Completa: *{home_team} vs {away_team}*"]
    lines.append("\n*🤖 Análise da Partida (TipsterIA)*")
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

def send_whatsapp_message(to_number, message_text):
    """Função para enviar mensagens usando a API da Meta."""
    url = f"https://graph.facebook.com/v18.0/{META_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to_number, "text": {"body": message_text}}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Erro ao enviar mensagem: {e.response.text}")

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == META_VERIFY_TOKEN:
            return request.args.get("hub.challenge"), 200
        else:
            return "Verification token mismatch", 403

    if request.method == "POST":
        try:
            data = request.get_json()
            if data and data.get("object") == "whatsapp_business_account":
                for entry in data.get("entry", []):
                    for change in entry.get("changes", []):
                        value = change.get("value", {})
                        if value and "messages" in value:
                            message_data = value.get("messages", [{}])[0]
                            from_number = message_data.get("from")
                            incoming_msg = message_data.get("text", {}).get("body", "").strip().lower()
                            response_text = ""

                            if not from_number or not incoming_msg:
                                continue

                            if from_number not in USER_STATE:
                                USER_STATE[from_number] = {'step': 'welcome'}
                            user_step = USER_STATE[from_number].get('step')

                            if incoming_msg.isdigit():
                                idx = int(incoming_msg) - 1
                                game_list_type = ""
                                if user_step == 'awaiting_pre_game_choice': game_list_type = 'pre_games'
                                elif user_step == 'awaiting_live_game_choice': game_list_type = 'live_games'
                                
                                if game_list_type:
                                    games = USER_STATE[from_number].get(game_list_type, [])
                                    if 0 <= idx < len(games):
                                        game = games[idx]; game_id = game.get('game_id')
                                        if game_list_type == 'pre_games':
                                            game_analysis = sba.analyze(game_id=game_id) if sba else None
                                            players_analysis = []
                                            if opta:
                                                team_ids = [game.get('teams', {}).get(side, {}).get('id') for side in ['home', 'away']]
                                                for team_id in team_ids:
                                                    if team_id:
                                                        players = opta.get_players_for_team(team_id=team_id)
                                                        if players:
                                                            analysis = opta.analyze_player(player_id=players[0]['id'])
                                                            if analysis: players_analysis.append(analysis)
                                            response_text = format_full_pre_game_analysis(game_analysis, players_analysis)
                                        else: # live_games
                                            radar_analysis = radar.stats_aovivo(game_id=game_id) if radar else None
                                            live_tips = sba.analyze_live_from_stats(radar_analysis) if sba and radar_analysis else []
                                            response_text = format_live_analysis(radar_analysis, live_tips)
                                        USER_STATE[from_number]['step'] = 'welcome'
                                    else:
                                        response_text = "Número inválido. Por favor, escolha um número da lista."
                                else:
                                    response_text = f"{get_greeting()}! Bem-vindo(a) ao Betting IA.\n\nEscolha uma opção:\n1. Jogos Pré-Live\n2. Jogos Ao Vivo"
                                    USER_STATE[from_number]['step'] = 'awaiting_menu_choice'
                            else:
                                if "pré" in incoming_msg or "pre" in incoming_msg or "1" == incoming_msg:
                                    all_games = sba.get_fixtures_for_dates(days_forward=0) if sba else []
                                    pre_games = [g for g in all_games if g.get('league', {}).get('id') in PRINCIPAL_LEAGUE_IDS and g['type'] == 'scheduled']
                                    if not pre_games:
                                        response_text = "Nenhum jogo pré-live das principais ligas encontrado para hoje."
                                    else:
                                        USER_STATE[from_number] = {'step': 'awaiting_pre_game_choice', 'pre_games': pre_games}
                                        lines = ["*Jogos Pré-Live (Principais Ligas):*\n"]
                                        for i, game in enumerate(pre_games[:20], start=1):
                                            lines.append(f"{i}. {game.get('teams', {}).get('home', {}).get('name', 'Time A')} x {game.get('teams', {}).get('away', {}).get('name', 'Time B')}")
                                        lines.append("\nDigite o número do jogo para receber a análise completa.")
                                        response_text = "\n".join(lines)
                                elif "vivo" in incoming_msg or "2" == incoming_msg:
                                    all_games = sba.get_fixtures_for_dates(days_forward=0) if sba else []
                                    live_games = [g for g in all_games if g.get('league', {}).get('id') in PRINCIPAL_LEAGUE_IDS and g['type'] == 'live']
                                    if not live_games:
                                        response_text = "Nenhum jogo das principais ligas acontecendo no momento."
                                    else:
                                        USER_STATE[from_number] = {'step': 'awaiting_live_game_choice', 'live_games': live_games}
                                        lines = ["*Jogos Acontecendo Agora (Principais Ligas):*\n"]
                                        for i, game in enumerate(live_games[:20], start=1):
                                            lines.append(f"{i}. {game.get('teams', {}).get('home', {}).get('name', 'Time A')} x {game.get('teams', {}).get('away', {}).get('name', 'Time B')}")
                                        lines.append("\nDigite o número do jogo para receber a análise ao vivo.")
                                        response_text = "\n".join(lines)
                                else:
                                    response_text = f"{get_greeting()}! Bem-vindo(a) ao Betting IA.\n\nEscolha uma opção:\n1. Jogos Pré-Live\n2. Jogos Ao Vivo"
                                    USER_STATE[from_number]['step'] = 'awaiting_menu_choice'
                            
                            if from_number and response_text:
                                send_whatsapp_message(from_number, response_text)
        except Exception:
            traceback.print_exc()
        return "OK", 200

    return "Not Found", 404
