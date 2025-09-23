
Betting IA ChatBot (WhatsApp) - Scaffold
--------------------------------------

Files included:
- app.py             : Flask + Twilio webhook
- sports_betting_analyzer.py : your analyzer (copied)
- radar_ia.py        : your radar module (copied)
- opta_ia.py         : your opta module (copied, if provided)
- requirements.txt
- Procfile
- .env.example

How to run locally:
1. Copy .env.example to .env and fill values (API key(s), Twilio credentials).
2. pip install -r requirements.txt
3. python app.py
4. Configure Twilio WhatsApp sandbox to point to https://your-server/webhook (POST).

Notes:
- The webhook uses simple command parsing. It caches the last "jogos" response per phone number in memory.
- The analyze functions try multiple likely function names in your modules. If your function names differ, edit app.py to call the real names.
- For production use, secure the app, add rate-limiting, persistent storage, user auth, subscription logic and billing.
