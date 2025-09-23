#!/usr/bin/env python3
"""
Simple Flask WhatsApp chatbot wrapper for Betting IA components.
- Exposes POST /webhook for Twilio WhatsApp to call when a message arrives.
- Supports commands (case-insensitive):
    "jogos hoje" or "jogos de hoje" -> lists today's matches via sports_betting_analyzer.get_games_by_sport wrapper
    "analisar <n>" -> analyze pre-game for match index n as returned previously
    "ao vivo <n>" -> live analysis for match index n (radar + tipster)
    "help" -> usage
Note: This project uses your modules copied into the package: sports_betting_analyzer.py, radar_ia.py, opta_ia.py
You must set environment variables (see .env.example) and configure Twilio to point to /webhook.
"""

import os, json, traceback
from flask import Flask, request, Response
from dotenv import load_dotenv
load_dotenv()

# try to import user modules
try:
    import sports_betting_analyzer as sba
except Exception as e:
    sba = None
try:
    import radar_ia as radar
except Exception as e:
    radar = None
try:
    import opta_ia as opta
except Exception as e:
    opta = None

# Twilio helper (format reply) using TwiML
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

# In-memory cache for last games per phone number (simple demo)
LAST_GAMES = {}

def safe_call(module, names, *args, **kwargs):
    \"\"\"Try to call first available function name from `names` in module.\"\"\"
    if not module:
        return None
    for n in names:
        fn = getattr(module, n, None)
        if callable(fn):
            try:
                return fn(*args, **kwargs)
            except Exception:
                # swallow and continue - caller should inspect results
                traceback.print_exc()
    return None

def list_games_for_sport(sport):
    # Wrapper: call possible get_games_by_sport or partidas-por-esporte style functions.
    # We'll try known function names from typical user file.
    names = [
        "get_games_by_sport", "partidas_por_esporte", "get_games_for_sport", "get_matches_by_sport",
        "partidas_por_esporte", "get_games_by_sport"
    ]
    # try module-level function
    res = safe_call(sba, names, sport)
    # if result is dict with 'response', normalize list
    if isinstance(res, dict) and "response" in res:
        return res.get("response") or []
    if isinstance(res, list):
        return res
    return []

def analyze_pre_game(game_id, sport):
    # call sba.analisar_pre_jogo or analyze_match etc.
    names = ["analisar_pre_jogo", "analyze_match", "analyze_pre_game", "analisar_pre_jogo"]
    return safe_call(sba, names, game_id, sport) or []

def analyze_live(game_id, sport):
    names = ["analisar_ao_vivo", "analyze_live", "analyze_in_play", "analisar_ao_vivo"]
    live = safe_call(sba, names, game_id, sport)
    # also try radar combined
    radar_info = safe_call(radar, ["live_summary", "analyze_live", "radar_live", "get_live_stats"], game_id) or {}
    if isinstance(live, list):
        return {"live": live, "radar": radar_info}
    return {"live": live or [], "radar": radar_info}

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        incoming = request.form.get("Body", "").strip()
        from_number = request.form.get("From", "unknown")
        # basic parsing
        text = incoming.lower()
        resp = MessagingResponse()
        msg = resp.message()
        if text in ("hi", "hello", "olá", "ola", "help"):
            msg.body(\"\"\"Olá! Comandos disponíveis:
- 'jogos hoje' -> lista jogos de hoje (football)
- 'analisar <n>' -> analisa pré-jogo do índice retornado
- 'ao vivo <n>' -> análise ao vivo do índice retornado
Use 'jogos hoje' primeiro e escolha o número do jogo (ex: 'analisar 1').\"\"\")
            return str(resp)
        if text.startswith("jogos"):
            # default to football
            sport = "football"
            games = list_games_for_sport(sport)
            if not games:
                msg.body(\"Nenhum jogo encontrado para hoje.\")
                return str(resp)
            # store for user
            LAST_GAMES[from_number] = games
            lines = [\"Jogos de hoje:\"]
            for i,g in enumerate(games[:20], start=1):
                try:
                    # each g might be dict with home/away/time
                    home = g.get('home') if isinstance(g, dict) else str(g)
                    away = g.get('away') if isinstance(g, dict) else ""
                    time = g.get('time', '') if isinstance(g, dict) else ""
                    lines.append(f\"{i}. {home} x {away} ({time})\")
                except Exception:
                    lines.append(str(g))
            lines.append(\"\\nResponda: 'analisar <n>' ou 'ao vivo <n>'\")
            msg.body(\"\\n\".join(lines))
            return str(resp)
        if text.startswith("analisar"):
            parts = text.split()
            if len(parts) < 2 or not parts[1].isdigit():
                msg.body(\"Uso: analisar <n>\")
                return str(resp)
            idx = int(parts[1]) - 1
            games = LAST_GAMES.get(from_number, [])
            if idx < 0 or idx >= len(games):
                msg.body(\"Índice inválido. Use 'jogos hoje' primeiro.\")
                return str(resp)
            g = games[idx]
            game_id = g.get('game_id') or g.get('fixture', {}).get('id') or g.get('id')
            sport = 'football'
            analysis = analyze_pre_game(game_id, sport)
            msg.body(f\"Análise pré-jogo:\\n{json.dumps(analysis, ensure_ascii=False, indent=2)[:1500]}\")
            return str(resp)
        if text.startswith("ao vivo"):
            parts = text.split()
            if len(parts) < 2 or not parts[1].isdigit():
                msg.body(\"Uso: ao vivo <n>\")
                return str(resp)
            idx = int(parts[1]) - 1
            games = LAST_GAMES.get(from_number, [])
            if idx < 0 or idx >= len(games):
                msg.body(\"Índice inválido. Use 'jogos hoje' primeiro.\")
                return str(resp)
            g = games[idx]
            game_id = g.get('game_id') or g.get('fixture', {}).get('id') or g.get('id')
            sport = 'football'
            analysis = analyze_live(game_id, sport)
            msg.body(f\"Análise ao vivo:\\n{json.dumps(analysis, ensure_ascii=False, indent=2)[:1500]}\")
            return str(resp)
        msg.body(\"Comando não reconhecido. Envie 'help' para ver comandos.\")
        return str(resp)
    except Exception as e:
        resp = MessagingResponse()
        resp.message(f\"Erro interno: {str(e)}\")
        return str(resp), 500

if __name__ == '__main__':
    # local dev run
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8000)), debug=True)
