# telegram-claude-remote

![CI](https://github.com/mateus9785/telegram-claude-remote/actions/workflows/ci.yml/badge.svg)

Um bot do Telegram que abre uma sessão do Claude Code com o Remote Control
ativado, acionada por um comando enviado do celular. Usa apenas long-polling
(sem webhook, sem endpoint HTTP público para expor). Foi construído para
rodar com PM2 em um servidor pessoal, sem TTY.

`/claude [prompt opcional]` inicia `claude --remote-control ... --bg` em um
diretório de trabalho fixo e responde com o nome da sessão; `/status` lista
as sessões ativas com um botão inline de "fechar" por sessão; `/reabrir <id>`
reinicia uma sessão cuja conexão do Remote Control caiu, retomando a
conversa de onde parou.

## Stack

- **Python 3.10+**, apenas biblioteca padrão além de uma dependência: **requests**
- **hatchling**: backend de build (detecta automaticamente o pacote
  `telegram_claude_remote/` na raiz do repositório, sem configuração extra)
- **ruff**: lint e formatação
- **mypy** (`disallow_untyped_defs = true`): todas as funções são tipadas
- **pytest**: testes unitários, com os limites de `subprocess`/sistema de
  arquivos mockados
- CI no GitHub Actions: `ruff check` → `ruff format --check` → `mypy` → `pytest`

## Arquitetura

```
bot.py                          <- thin entrypoint (what `pm2 start bot.py` runs)
telegram_claude_remote/
  config.py                      paths, timeouts, .env/.offset persistence, BotState
  telegram_client.py             Telegram Bot API: send message, answer callback, getUpdates
  claude_control.py              subprocess control of the `claude` CLI (launch/list/close/reopen)
  handlers.py                    per-command handlers + update dispatch
  main.py                        bootstrap: load .env, long-poll loop
```

`bot.py` continua na raiz do repositório como um shim de uma linha
(`from telegram_claude_remote.main import main`), para que o
`pm2 start bot.py` já usado em produção continue funcionando. A divisão em
pacote por baixo dele é invisível para o process manager.

O estado de autorização (qual chat pode enviar comandos) fica em uma
instância de `BotState` criada uma vez em `main()` e passada explicitamente
para cada handler, em vez de uma variável global de módulo alterada via
`global`. A versão original em arquivo único usava essa segunda abordagem,
o que deixava a dependência real de cada handler invisível.

## Decisões Técnicas

- **Pacote em subpasta, não módulos soltos na raiz do repositório.** Fica
  mais fácil de entender para quem abre o repositório pela primeira vez, e é
  o layout que o hatchling reconhece automaticamente, sem configuração extra
  em `[tool.hatch.build]`.
- **Objeto `BotState` em vez de uma variável global de módulo.** O `bot.py`
  original lia e escrevia diretamente a global `OWNER_CHAT_ID = None` usando
  a palavra-chave `global` dentro de `handle_start`. Um objeto explícito
  passado pela assinatura de cada handler torna a dependência visível em
  cada ponto de chamada, em vez de escondida dentro do corpo de uma função.
- **`dict[str, Any]` para os payloads do Telegram, em vez de um `TypedDict`
  modelado.** Tipar completamente o formato de `update`/`callback_query` da
  Bot API seria um esforço desproporcional para um bot pessoal pequeno, com
  apenas cinco comandos.
- **Os testes mockam no limite de `subprocess`/sistema de arquivos, não em
  um nível mais alto.** Todo teste que exercita `claude_control` faz patch
  direto de `subprocess.run`, `os.kill` ou `shutil.rmtree`, em vez das
  funções que os chamam. Assim, uma falha de teste indica que a lógica do
  wrapper está errada, não que um mock ficou desatualizado em relação ao
  que deveria simular.
- **Sem modo webhook.** Long-polling é mais simples de rodar com PM2 (sem
  endpoint HTTPS público, sem proxy reverso, sem certificado TLS para
  gerenciar), e o bot sempre tem apenas um operador: a latência extra do
  polling não é um problema aqui.

## Segurança

Este bot foi construído para **uso pessoal, com um único operador, em um
servidor em que você já confia**. Com base nisso, ele faz algumas concessões
que precisariam ser reavaliadas antes de reutilizar o código em outro
contexto:

- **`claude --dangerously-skip-permissions`.** Toda sessão aberta por
  `/claude` roda o Claude Code com os prompts de permissão desativados:
  acesso completo ao sistema de arquivos dentro do diretório de trabalho
  configurado, sem sandboxing por ação. Esse é justamente o propósito do
  bot (acionar remotamente um agente que consegue realizar ações reais, sem
  precisar aprovar cada passo pelo celular), mas isso significa que qualquer
  pessoa capaz de falar com o bot pode fazer o Claude Code executar qualquer
  coisa que o usuário do host possa executar.
- **O modelo de autorização é de dono único, travado no primeiro contato.**
  O chat que enviar `/start` primeiro se torna o `OWNER_CHAT_ID`, persistido
  em `.env`, e a partir daí qualquer outro chat é rejeitado. Não há suporte
  a múltiplos usuários, nem rotação de token, e a única forma de trocar o
  dono é editando o `.env` manualmente. É um modelo deliberadamente mínimo,
  pensado para um bot com exatamente um usuário previsto.
- **`/status` pode enviar `SIGTERM` para processos arbitrários por pid.** O
  botão de "fechar sessão" envia `os.kill(pid, SIGTERM)` para o que
  `claude agents --json` reportar, restrito aos processos descobertos dessa
  forma. Não é um primitivo genérico de "matar qualquer pid", mas ainda é
  uma capacidade real protegida apenas pela autenticação do Telegram.

Essas escolhas são aceitáveis **para o modelo de ameaça específico deste
bot**: um operador, um servidor pessoal já isolado, sem outros inquilinos.
**Não copie esse padrão de autorização para um contexto multiusuário ou de
infraestrutura compartilhada** sem antes redesenhar o modelo de autenticação
(tokens por usuário, permissões escopadas, log de auditoria). Nada aqui foi
endurecido para esse cenário.

## Configuração

Requer o CLI `claude` instalado e disponível no `PATH`, além de um token de
bot do Telegram obtido com o [@BotFather](https://t.me/BotFather).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # fill in TELEGRAM_BOT_TOKEN; leave OWNER_CHAT_ID empty

python3 bot.py          # or: pm2 start bot.py
```

Envie `/start` para o bot a partir da conta do Telegram que você quer como
dono: isso trava o `OWNER_CHAT_ID` no `.env` no primeiro contato.

`HOME_DIR` (`telegram_claude_remote/config.py`) tem o valor fixo
`/home/ubuntu`, o diretório de trabalho onde `claude --remote-control` é
iniciado. Este bot foi construído para rodar em um servidor específico, sem
ser pensado para ser portável entre hosts imediatamente (veja
[Limitações conhecidas](#limitações-conhecidas--próximos-passos)).

## Testes

```bash
pytest -v
```

38 testes unitários, todos com as chamadas de `subprocess`/sistema de
arquivos mockadas: nenhum teste usa um token real do Telegram, faz polling
na API real, ou executa o binário `claude` de verdade.

## Limitações conhecidas / Próximos passos

- `HOME_DIR` é uma constante fixa no código, não lida do `.env`: mover este
  bot para outro servidor exige editar `config.py`, não só o `.env`.
- Autorização apenas de dono único; sem suporte a múltiplos usuários (veja
  [Segurança](#segurança) para entender por que essa é uma escolha
  deliberada, não um descuido).
- Sem estratégia de retry/backoff além de um sleep fixo de 5 segundos quando
  uma requisição `getUpdates` falha. Isso é suficiente para o loop de
  polling de um bot pessoal, mas exigiria um backoff de verdade sob carga
  maior ou uma rede mais instável.
- Os payloads do Telegram são tipados como `dict[str, Any]`, em vez de
  `TypedDict`s modelados (veja
  [Decisões Técnicas](#decisões-técnicas)).

## Licença

[MIT](./LICENSE)
