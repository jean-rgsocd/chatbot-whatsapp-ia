# app.py (VERSÃO CORRIGIDA E INTEGRADA)
import os
import json
import traceback
from flask import Flask, request
from dotenv import load_dotenv
from twilio.twiml.messaging_response import MessagingResponse

# Carrega variáveis de ambiente do .env (para testes locais)
load_dotenv()

# Tenta importar os módulos de análise
try:
    import sports_betting_analyzer as sba
except ImportError:
    print("AVISO: Módulo 'sports_betting_analyzer' não encontrado.")
    sba = None
try:
    import radar_ia as radar
except ImportError:
    print("AVISO: Módulo 'radar_ia' não encontrado.")
    radar = None
# O módulo opta_ia não é usado nos comandos atuais, mas mantemos o import
try:
    import opta_ia as opta
except ImportError:
    opta = None

app = Flask(__name__)

# Cache simples na memória para guardar a última lista de jogos por usuário
LAST_GAMES = {}

def format_pre_game_analysis(analysis: dict) -> str:
    """Formata a análise pré-jogo para uma mensagem clara no WhatsApp."""
    if not analysis or 'summary' not in analysis:
        return "Não foi possível obter a análise para este jogo."

    summary = analysis.get('summary', {})
    top3 = analysis.get('top3', [])
    
    home_team = summary.get('home_team', 'Casa')
    away_team = summary.get('away_team', 'Visitante')

    lines = [
        f"Análise Pré-Jogo: *{home_team} vs {away_team}*",
        f"Poder de Fogo (Casa): {summary.get('home_power', 'N/A')}",
        f"Poder de Fogo (Visitante): {summary.get('away_power', 'N/A')}",
        "---",
        "*Principais Palpites:*",
    ]

    for i, pick in enumerate(top3, 1):
        market = pick.get('market', '').replace('_', ' ').title()
        rec = pick.get('recommendation', 'N/A')
        conf = pick.get('confidence', 0)
        odd = pick.get('best_odd', None)
        
        line = f"{i}. *{market}*: {rec} (Confiança: {conf:.0%})"
        if odd:
            line += f" - Melhor Odd: *{odd}*"
        lines.append(line)
        
    lines.append("\n_Lembre-se: analise por conta própria. Odds podem variar._")
    return "\n".join(lines)

def format_live_analysis(analysis: dict) -> str:
    """Formata a análise ao vivo para uma mensagem clara no WhatsApp."""
    if not analysis or 'fixture' not in analysis:
        return "Não foi possível obter os dados ao vivo para este jogo."

    fixture = analysis.get('fixture', {})
    teams = analysis.get('teams', {})
    score = analysis.get('score', {}).get('fulltime', {})
    status = fixture.get('status', {})
    stats = analysis.get('statistics', {}).get('home', {}) # Pegando stats da casa como exemplo

    home_team = teams.get('home', {}).get('name', 'Casa')
    away_team = teams.get('away', {}).get('name', 'Visitante')
    home_score = score.get('home', 0)
    away_score = score.get('away', 0)
    elapsed = status.get('elapsed', '?')

    lines = [
        f"Análise Ao Vivo: *{home_team} {home_score} x {away_score} {away_team}* ({elapsed}')",
        "---",
        f"*Estatísticas (Casa):*",
        f"Chutes: {stats.get('total_shots', 'N/A')}",
        f"Posse de Bola: {stats.get('possession', 'N/A')}",
        f"Escanteios: {stats.get('corners', 'N/A')}",
        "---",
        "*Eventos Recentes:*"
    ]
    
    events = analysis.get('events', [])
    for event in events[:5]: # Pega os 5 eventos mais recentes
        time = event.get('display_time', '')
        category = event.get('category', 'Evento')
        player = event.get('player', '')
        detail = event.get('detail', '')
        lines.append(f"- {time} *{category}*: {player} ({detail})")

    return "\n".join(lines)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        incoming_msg = request.form.get("Body", "").strip()
        from_number = request.form.get("From", "unknown")
        text = incoming_msg.lower()
        
        resp = MessagingResponse()
        msg = resp.message()

        if text in ("hi", "hello", "olá", "ola", "help", "ajuda"):
            msg.body(
                "Olá! Comandos disponíveis:\n"
                "- *'jogos hoje'* -> Lista os principais jogos de hoje.\n"
                "- *'analisar <n>'* -> Faz uma análise pré-jogo completa.\n"
                "- *'ao vivo <n>'* -> Mostra estatísticas da partida em tempo real."
            )
            return str(resp)

        if text.startswith("jogos"):
            # CHAMA A FUNÇÃO CORRETA: sba.get_fixtures_for_dates
            games = sba.get_fixtures_for_dates(days_forward=0) if sba else []
            
            if not games:
                msg.body("Nenhum jogo encontrado para hoje.")
                return str(resp)

            LAST_GAMES[from_number] = games
            lines = ["*Jogos de hoje:*"]
            for i, game in enumerate(games[:20], start=1):
                home = game.get('teams', {}).get('home', {}).get('name', 'Time A')
                away = game.get('teams', {}).get('away', {}).get('name', 'Time B')
                league = game.get('league', {}).get('name', 'Liga')
                lines.append(f"{i}. {home} x {away} ({league})")
            
            lines.append("\nResponda com 'analisar <n>' ou 'ao vivo <n>' (ex: analisar 1)")
            msg.body("\n".join(lines))
            return str(resp)

        if text.startswith("analisar"):
            parts = text.split()
            if len(parts) < 2 or not parts[1].isdigit():
                msg.body("Comando inválido. Use o formato: *analisar 1*")
                return str(resp)
            
            idx = int(parts[1]) - 1
            games = LAST_GAMES.get(from_number, [])
            
            if not (0 <= idx < len(games)):
                msg.body("Índice de jogo inválido. Envie 'jogos hoje' primeiro para ver a lista.")
                return str(resp)
            
            game = games[idx]
            game_id = game.get('game_id')
            
            if not game_id:
                 msg.body("Não foi possível encontrar o ID deste jogo para análise.")
                 return str(resp)
            
            # CHAMA A FUNÇÃO CORRETA: sba.analyze
            analysis = sba.analyze(game_id=game_id) if sba else None
            formatted_response = format_pre_game_analysis(analysis)
            msg.body(formatted_response)
            return str(resp)

        if text.startswith("ao vivo"):
            parts = text.split()
            if len(parts) < 2 or not parts[1].isdigit():
                msg.body("Comando inválido. Use o formato: *ao vivo 1*")
                return str(resp)
                
            idx = int(parts[1]) - 1
            games = LAST_GAMES.get(from_number, [])

            if not (0 <= idx < len(games)):
                msg.body("Índice de jogo inválido. Envie 'jogos hoje' primeiro para ver a lista.")
                return str(resp)

            game = games[idx]
            game_id = game.get('game_id')

            if not game_id:
                 msg.body("Não foi possível encontrar o ID deste jogo para análise.")
                 return str(resp)

            # CHAMA A FUNÇÃO CORRETA: radar.stats_aovivo
            analysis = radar.stats_aovivo(game_id=game_id) if radar else None
            formatted_response = format_live_analysis(analysis)
            msg.body(formatted_response)
            return str(resp)

        msg.body("Comando não reconhecido. Envie 'ajuda' para ver os comandos disponíveis.")
        return str(resp)

    except Exception as e:
        traceback.print_exc()
        resp = MessagingResponse()
        resp.message(f"Ocorreu um erro interno ao processar seu pedido. Tente novamente mais tarde.")
        return str(resp), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=True)
