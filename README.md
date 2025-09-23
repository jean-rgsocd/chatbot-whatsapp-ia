# Betting IA — Tipster Unificado

Este projeto integra:
- Sports Betting Analyzer (pré-live)
- Radar IA (ao vivo)
- Opta IA (estatísticas de jogadores)

---

## 🚀 Rodando localmente

1. Crie um ambiente virtual (opcional, mas recomendado):
   ```bash
   python -m venv venv
   source venv/bin/activate   # Linux/macOS
   venv\Scripts\activate      # Windows
   ```

2. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```

3. Crie o arquivo `.env` com sua chave da API-Football:
   ```
   API_SPORTS_KEY=your_api_key_here
   ```

4. Execute:
   ```bash
   python tipster.py
   ```

O servidor vai rodar em `http://0.0.0.0:5000`.

---

## ☁️ Deploy no Render

1. Confirme que o arquivo `Procfile` está na raiz do projeto com o conteúdo:
   ```
   web: gunicorn tipster:app
   ```

2. Dê deploy no Render.  
   Ele vai instalar as dependências do `requirements.txt` e rodar o `gunicorn`.

---

## 📡 Endpoints disponíveis

- `GET /fixtures?date=YYYY-MM-DD` → Jogos do dia  
- `GET /fixtures/live` → Jogos ao vivo  
- `POST /analyze/game` → Análise pré-live  
- `POST /analyze/live` → Análise ao vivo  
- `GET /players?id=PLAYER_ID&season=YYYY` → Estatísticas de jogador  
