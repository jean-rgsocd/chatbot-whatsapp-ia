Betting IA ChatBot (WhatsApp)

ChatBot de apostas esportivas usando Flask (API no Render) + whatsapp-web.js (cliente local).
O sistema é dividido em duas partes:

Cozinha (Render) → API em Flask com análise de jogos.

Garçom (PC Local) → Bot WhatsApp rodando com whatsapp-web.js, que conecta ao WhatsApp Web e conversa com a Cozinha via API.

📂 Arquivos incluídos

app.py → API Flask principal (Cozinha).

sports_betting_analyzer.py → módulo de análises.

radar_ia.py → módulo de radar (estatísticas ao vivo).

opta_ia.py → módulo de dados Opta.

requirements.txt → dependências para rodar no Render.

Procfile → instrução de execução no Render (gunicorn app:app).

index.js → cliente WhatsApp local usando whatsapp-web.js (Garçom).

.env.example → modelo de variáveis de ambiente.

▶️ Como rodar localmente (API Flask)

Clone o repositório e entre na pasta:

git clone https://github.com/SEU_USER/chatbot-whatsapp-ia.git
cd chatbot-whatsapp-ia


Crie o arquivo .env baseado em .env.example e configure suas chaves:

API_SPORTS_KEY

(opcional) outras integrações

Instale dependências:

pip install -r requirements.txt


Rode localmente a API:

python app.py

🚀 Como rodar no Render (Cozinha)

Faça push do código para o GitHub.

Conecte o repositório ao Render.

Configure as variáveis de ambiente em Settings > Environment.

Deploy automático:

O Render vai usar o Procfile para rodar:

web: gunicorn app:app


Após o deploy, a API ficará acessível em:

https://chatbot-whatsapp-ia-xxxx.onrender.com

🤖 Como rodar o Bot do WhatsApp (Garçom)

No PC local, entre na pasta do bot:

cd "C:\ChatBOTBettingIA"


Configure a URL da API (Render):

set PYTHON_API_URL=https://chatbot-whatsapp-ia-xxxx.onrender.com


Instale dependências Node.js:

npm install


Rode o bot:

node index.js


Escaneie o QR Code que aparecer no terminal com seu WhatsApp (Configurações > Aparelhos Conectados).

Agora o bot estará ativo no seu número.

📝 Fluxo de uso no WhatsApp

Digite oi → recebe menu inicial.

Digite 1 → lista jogos pré-live.

Escolha um jogo (ex: 2) → análise pré-live.

Digite 3 → pede ID de jogo ao vivo para análise.

Qualquer outra mensagem → retorna menu.

⚠️ Notas importantes

O sistema depende de duas partes:

Render (API Flask).

Bot local (Node.js com whatsapp-web.js).

Sempre que limpar a sessão do WhatsApp, será necessário escanear o QR Code de novo.

Para produção, recomenda-se:

Persistência em banco de dados.

Controle de usuários.

Autenticação e segurança.

Rate limiting.
