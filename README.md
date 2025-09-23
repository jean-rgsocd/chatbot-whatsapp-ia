# 📑 Master Guide — Tipster IA (`tipster.py`)

### 🔑 1. Pré-jogo (Sports Betting Analyzer)
- **Fonte:** `fixtures` + `fixtures/statistics`  
- **Placar usado:** não usa (pré-jogo não tem gols)  
- **Stats:** `Shots on Goal`, `Total Shots`, etc.  
- **Funções principais:**  
  - `get_fixtures_for_dates` → lista jogos futuros/hoje  
  - `heuristics_football` → monta picks com base nos remates  
  - `analyze` / `analyze_game` → consolida análise pré-jogo  

---

### ⚡ 2. Ao vivo (Opção 3 — análise rápida live)
- **Fonte:** `stats_aovivo` (RadarIA)  
- **Placar:** `fixture.goals.home` / `fixture.goals.away` ✅  
  - Fallback: `fixture.score.home` / `fixture.score.away`  
- **Tempo:** `status.elapsed`  
- **Funções principais:**  
  - `analyze_live_from_stats` → gera picks em tempo real  
  - `format_live_analysis` → texto bonito pro usuário (WhatsApp/bot)  

---

### 📡 3. Radar IA (Opção 4 — estatísticas ao vivo detalhadas)
- **Fonte:**  
  - `fixtures` → dados do jogo  
  - `fixtures/statistics` → estatísticas normalizadas  
  - `fixtures/events` → eventos (gols, cartões, escanteios, etc.)  
- **Placar:** também `fixture.goals` ✅  
- **Função:** `stats_aovivo` → retorna stats+eventos processados  
- **Eventos recentes:** processados com `_format_display_time` e `classify_event`  

---

### 👤 4. Opta IA (Opção 5 — análise de jogadores)
- **Fonte:** `players` (API-FOOTBALL)  
- **Placar:** não usa  
- **Stats do jogador:** `goals`, `shots`, `assists`, `passes`, etc.  
- **Funções principais:**  
  - `get_players_for_team` → lista jogadores  
  - `analyze_player` → coleta dados brutos  
  - `process_and_analyze_stats` → gera recomendações por mercado  
  - `format_player_analysis` → saída legível  

---

### ⚙️ 5. Helpers globais
- **Cache:** `_cache_get` / `_cache_set`  
- **HTTP:** `api_get_raw` / `safe_get`  
- **Converters:** `safe_int`, `safe_float`, `format_conf_pct`  

---

# 🚀 Resumo do que importa
- **Placar ao vivo:** sempre via `fixture.goals` (garantido ✅)  
- **Tempo de jogo:** `status.elapsed`  
- **Fallbacks:**  
  - Se gols zerados → tenta `fixture.score`  
  - Se stats faltando → `get_stat` com várias chaves alternativas  
- **Consistência:**  
  - Todos os modos (Pré, Ao vivo, Radar, Opta) já usam as mesmas bases, só que adaptadas ao contexto.  
