# Monitor RK9 → Discord

Avisa num canal de Discord quando abrem inscrições TCG em torneios Pokémon europeus (Regionais, Specials, EUIC) e no Mundial, a partir de https://rk9.gg/events/pokemon.

## Avisos
- 🆕 Evento novo no âmbito
- 📅 Hora de abertura anunciada
- ⏰ 15 minutos antes da abertura
- ✅ Inscrições TCG abertas

## Instalação
1. Discord: Definições do canal → Integrações → Webhooks → Novo webhook → Copiar URL.
2. GitHub: cria um repositório **público** e envia estes ficheiros (incluindo a pasta `.github`). Público porque os minutos do Actions são gratuitos sem limite em repositórios públicos; num privado, 5 em 5 minutos gasta mais do que a quota gratuita. O URL do webhook fica num secret, não fica visível.
3. No repositório: Settings → Secrets and variables → Actions → New repository secret
   - Nome: `DISCORD_WEBHOOK_URL`, valor: o URL do passo 1.
4. (Opcional) Na mesma página, separador Variables: `DISCORD_MENTION` = `@everyone` ou `<@TEU_ID>`.
5. Actions → RK9 monitor → Run workflow. Deve chegar ao Discord a mensagem "Monitor RK9 ligado".

Depois disso corre sozinho a cada 5 minutos (o GitHub pode atrasar execuções agendadas).

## Testar localmente sem enviar
```
pip install -r requirements.txt
DRY_RUN=1 python rk9_monitor.py
```

## Mudar o âmbito
Edita `in_scope()` e o conjunto `EUROPE` em `rk9_monitor.py`.
