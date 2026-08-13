#!/usr/bin/env python3
"""
Bot Telegram que abre uma sessao do Claude Code com Remote Control ativado
na pasta principal do usuario (/home/ubuntu, no servidor Lightsail), disparada por comando.

Fluxo:
  1. Long-polling no Telegram (getUpdates), offset salvo em .telegram_offset.
  2. Primeiro /start que chegar trava o bot nesse chat_id (grava em .env como
     OWNER_CHAT_ID) - dai em diante so esse chat consegue usar os comandos.
  3. /claude [texto opcional] roda:
       claude --remote-control <nome> --bg --dangerously-skip-permissions [texto]
     com cwd=/home/ubuntu, e responde com o nome da sessao criada.
  4. /status roda `claude agents --json` e resume as sessoes ativas, com um
     botao inline "Fechar" por sessao (callback_data "fechar:<pid>") que
     manda SIGTERM no processo.
  5. /pastas lista as subpastas de primeiro nivel de /home/ubuntu (sem contar
     as ocultas).

Uso: python3 bot.py (pensado para rodar sob pm2, sem TTY).
"""

import json
import os
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
OFFSET_PATH = os.path.join(SCRIPT_DIR, ".telegram_offset")

HOME_DIR = "/home/ubuntu"
JOBS_DIR = os.path.join(HOME_DIR, ".claude", "jobs")

POLL_TIMEOUT = 30
CLAUDE_LAUNCH_TIMEOUT = 60
CLAUDE_STATUS_TIMEOUT = 20
CLAUDE_REOPEN_TIMEOUT = 60

# tira os códigos de cor ANSI da saída do `claude --bg` pra achar o id novo
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

OWNER_CHAT_ID = None  # carregado do .env em main(), pode ser travado em runtime


def load_env():
    valores = {}
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            chave, _, valor = line.partition("=")
            valores[chave.strip()] = valor.strip()
    return valores


def set_owner_chat_id(chat_id):
    """Reescreve a linha OWNER_CHAT_ID=... no .env, preservando o resto do arquivo."""
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        linhas = f.readlines()

    nova_linha = f"OWNER_CHAT_ID={chat_id}\n"
    escrito = False
    for i, linha in enumerate(linhas):
        if linha.strip().startswith("OWNER_CHAT_ID="):
            linhas[i] = nova_linha
            escrito = True
            break
    if not escrito:
        linhas.append(nova_linha)

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(linhas)


def load_offset():
    if os.path.exists(OFFSET_PATH):
        with open(OFFSET_PATH, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                return int(content)
    return None


def save_offset(offset):
    with open(OFFSET_PATH, "w", encoding="utf-8") as f:
        f.write(str(offset))


def is_owner(chat_id):
    return OWNER_CHAT_ID is not None and str(chat_id) == str(OWNER_CHAT_ID)


def enviar_mensagem(base_url, chat_id, texto, reply_markup=None):
    payload = {"chat_id": chat_id, "text": texto}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{base_url}/sendMessage", json=payload, timeout=15)
    except requests.RequestException as e:
        print(f"[erro ao enviar para {chat_id}] {e}")


def responder_callback(base_url, callback_query_id, texto=None, show_alert=False):
    payload = {"callback_query_id": callback_query_id}
    if texto:
        payload["text"] = texto
    if show_alert:
        payload["show_alert"] = True
    try:
        requests.post(f"{base_url}/answerCallbackQuery", json=payload, timeout=15)
    except requests.RequestException as e:
        print(f"[erro ao responder callback] {e}")


def handle_start(base_url, chat_id):
    global OWNER_CHAT_ID
    if OWNER_CHAT_ID is None:
        OWNER_CHAT_ID = str(chat_id)
        set_owner_chat_id(OWNER_CHAT_ID)
        print(f"[acesso travado] OWNER_CHAT_ID={OWNER_CHAT_ID}")
        enviar_mensagem(
            base_url, chat_id,
            "Acesso travado neste chat — a partir de agora só respondo comandos "
            "vindos daqui.\n\n"
            "/claude [instrução opcional] — abre uma sessão do Claude Code em "
            "/home/ubuntu com Remote Control ativado (pode continuar pelo app do "
            "Claude Code).\n"
            "/status — lista as sessões ativas, com botão para fechar cada uma "
            "(e 🔓 Reabrir nas que bloquearam e perderam o acesso remoto).\n"
            "/reabrir <id> — reativa o Remote Control de uma sessão bloqueada, "
            "resumindo a conversa de onde parou.\n"
            "/pastas — lista as pastas de primeiro nível em /home/ubuntu.",
        )
        return

    if is_owner(chat_id):
        enviar_mensagem(base_url, chat_id, "Você já é o dono autorizado deste bot.")
    else:
        enviar_mensagem(base_url, chat_id, "🔒 Não autorizado.")


def handle_claude(base_url, chat_id, prompt):
    if not is_owner(chat_id):
        enviar_mensagem(base_url, chat_id, "🔒 Não autorizado.")
        return

    session_name = f"tg-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    cmd = ["claude", "--remote-control", session_name, "--bg", "--dangerously-skip-permissions"]
    if prompt:
        cmd.append(prompt)

    print(f"[lançando] {cmd} (cwd={HOME_DIR})")
    try:
        result = subprocess.run(cmd, cwd=HOME_DIR, capture_output=True, text=True, timeout=CLAUDE_LAUNCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        enviar_mensagem(base_url, chat_id, "Tempo esgotado tentando abrir a sessão. Tente /status para conferir.")
        return

    if result.returncode != 0:
        erro = (result.stderr or result.stdout).strip()
        print(f"[erro claude --remote-control] {erro}")
        enviar_mensagem(base_url, chat_id, f"Falha ao abrir a sessão:\n{erro[:1500]}")
        return

    msg = f"✅ Sessão \"{session_name}\" aberta em {HOME_DIR}"
    enviar_mensagem(base_url, chat_id, msg)


def handle_status(base_url, chat_id):
    if not is_owner(chat_id):
        enviar_mensagem(base_url, chat_id, "🔒 Não autorizado.")
        return

    try:
        result = subprocess.run(
            ["claude", "agents", "--json"],
            cwd=HOME_DIR, capture_output=True, text=True, timeout=CLAUDE_STATUS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        enviar_mensagem(base_url, chat_id, "Tempo esgotado consultando as sessões.")
        return

    if result.returncode != 0:
        enviar_mensagem(base_url, chat_id, f"Erro ao consultar sessões:\n{result.stderr.strip()[:1500]}")
        return

    try:
        sessoes = json.loads(result.stdout)
    except json.JSONDecodeError:
        enviar_mensagem(base_url, chat_id, "Não consegui interpretar a resposta do `claude agents --json`.")
        return

    if not sessoes:
        enviar_mensagem(base_url, chat_id, "Nenhuma sessão ativa no momento.")
        return

    linhas = []
    botoes = []
    for s in sessoes:
        inicio = datetime.fromtimestamp(s["startedAt"] / 1000).strftime("%d/%m %H:%M") if s.get("startedAt") else "?"
        sid = s.get("id")
        # nem toda sessão traz "name" (ex.: bg bloqueada que já encerrou o processo);
        # cai pro id, que sempre existe, em vez de "(sem nome)".
        nome = s.get("name") or sid or "(sem nome)"
        estado = s.get("state") or s.get("status")
        linha = f"• {nome} [{s.get('kind', '?')}] — {s.get('cwd', '?')} (desde {inicio})"
        if estado:
            linha += f" · {estado}"
        linhas.append(linha)
        # botão por id (não por pid): assim sessões-fantasma sem processo também
        # podem ser fechadas/limpas — o pid é resolvido na hora de fechar.
        if sid:
            fila = [{"text": f"🛑 Fechar {nome}", "callback_data": f"fechar:{sid}"}]
            # sessão sem pid = fantasma bloqueado que perdeu o Remote Control;
            # oferece reabrir (respawna resumindo a conversa, com RC de novo).
            if not s.get("pid"):
                fila.insert(0, {"text": f"🔓 Reabrir {nome}", "callback_data": f"reabrir:{sid}"})
            botoes.append(fila)

    reply_markup = {"inline_keyboard": botoes} if botoes else None
    enviar_mensagem(base_url, chat_id, "Sessões ativas:\n" + "\n".join(linhas), reply_markup=reply_markup)


def handle_pastas(base_url, chat_id):
    if not is_owner(chat_id):
        enviar_mensagem(base_url, chat_id, "🔒 Não autorizado.")
        return

    try:
        pastas = sorted(
            entry.name for entry in os.scandir(HOME_DIR)
            if entry.is_dir() and not entry.name.startswith(".")
        )
    except OSError as e:
        enviar_mensagem(base_url, chat_id, f"Erro ao listar pastas: {e}")
        return

    if not pastas:
        enviar_mensagem(base_url, chat_id, f"Nenhuma pasta encontrada em {HOME_DIR}.")
        return

    texto = f"Pastas em {HOME_DIR}:\n" + "\n".join(f"• {p}" for p in pastas)
    enviar_mensagem(base_url, chat_id, texto)


def carregar_state(session_id):
    """Lê o state.json do job dessa sessão (guarda resumeSessionId, respawnFlags, bridge)."""
    caminho = os.path.join(JOBS_DIR, session_id, "state.json")
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def montar_url_remote(bridge_session_id):
    """Monta a URL de continuar no app a partir do bridgeSessionId (cse_... -> session_...)."""
    if not bridge_session_id:
        return None
    sid = bridge_session_id
    if sid.startswith("cse_"):
        sid = sid[len("cse_"):]
    return f"https://claude.ai/code/session_{sid}"


def reabrir_sessao(session_id):
    """Respawna uma sessão bg bloqueada (fantasma, sem processo) com o Remote Control
    reativado, resumindo a conversa de onde parou. O `claude --resume ... --bg` cria um
    id de job NOVO e deixa o fantasma antigo pra trás, então limpamos o antigo aqui (a
    conversa em si fica salva em .claude/projects/, não no job dir).
    Retorna (ok, novo_id, url, erro)."""
    state = carregar_state(session_id)
    if not state:
        return False, None, None, "não encontrei o estado dessa sessão (pode já ter sido limpa)."

    resume_id = state.get("resumeSessionId") or state.get("sessionId") or session_id
    flags = state.get("respawnFlags") or ["--remote-control", "--dangerously-skip-permissions"]
    cmd = ["claude", "--resume", resume_id, *flags, "--bg"]

    print(f"[reabrindo] {cmd} (cwd={HOME_DIR})")
    try:
        result = subprocess.run(
            cmd, cwd=HOME_DIR, capture_output=True, text=True, timeout=CLAUDE_REOPEN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, None, None, "tempo esgotado ao reabrir. Confira com /status."

    if result.returncode != 0:
        return False, None, None, (result.stderr or result.stdout).strip()[:1500]

    # a saída é tipo "backgrounded · <id novo> (idle — send a prompt to start)"
    saida = ANSI_RE.sub("", result.stdout)
    m = re.search(r"backgrounded\s*·?\s*([0-9a-f]{6,})", saida)
    novo_id = m.group(1) if m else None

    # remove o fantasma antigo pra não aparecer duplicado no /status
    if novo_id and novo_id != session_id:
        antigo = os.path.join(JOBS_DIR, session_id)
        if os.path.isdir(antigo):
            try:
                shutil.rmtree(antigo)
            except OSError as e:
                print(f"[aviso: não limpei o fantasma {session_id}] {e}")

    # a bridge do Remote Control conecta de forma assíncrona; espera a URL aparecer
    url = None
    if novo_id:
        for _ in range(8):
            ns = carregar_state(novo_id)
            url = montar_url_remote(ns.get("bridgeSessionId")) if ns else None
            if url:
                break
            time.sleep(1)

    return True, novo_id, url, None


def buscar_sessao_por_id(session_id):
    """Consulta `claude agents --json` e devolve o dict da sessão com esse id, ou None."""
    try:
        result = subprocess.run(
            ["claude", "agents", "--json"],
            cwd=HOME_DIR, capture_output=True, text=True, timeout=CLAUDE_STATUS_TIMEOUT,
        )
        sessoes = json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return None
    for s in sessoes:
        if str(s.get("id")) == str(session_id):
            return s
    return None


def handle_fechar_callback(base_url, callback_query):
    callback_id = callback_query["id"]
    chat_id = callback_query["message"]["chat"]["id"]

    if not is_owner(chat_id):
        responder_callback(base_url, callback_id, "🔒 Não autorizado.", show_alert=True)
        return

    dados = callback_query.get("data", "")
    _, _, session_id = dados.partition(":")
    session_id = session_id.strip()
    # ids de sessão são curtos e alfanuméricos (hex + '-'); recusa qualquer coisa
    # fora disso pra não montar caminho de job com '/' ou '..'.
    if not session_id or not all(c.isalnum() or c == "-" for c in session_id):
        responder_callback(base_url, callback_id, "Sessão inválida.", show_alert=True)
        return

    sessao = buscar_sessao_por_id(session_id)
    pid = sessao.get("pid") if sessao else None

    if pid:
        # sessão com processo vivo: SIGTERM e deixa o daemon limpar o registro sozinho.
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pid = None  # processo já saiu — cai na limpeza do estado abaixo
        except (PermissionError, ValueError):
            responder_callback(base_url, callback_id, "Sem permissão para encerrar essa sessão.", show_alert=True)
            return

    if not pid:
        # sessão-fantasma (bloqueada, sem processo): remove o estado do job pra
        # tirá-la da lista do `claude agents`.
        job_dir = os.path.join(JOBS_DIR, session_id)
        if os.path.isdir(job_dir):
            try:
                shutil.rmtree(job_dir)
            except OSError as e:
                responder_callback(base_url, callback_id, f"Falha ao limpar a sessão: {e}", show_alert=True)
                return
        else:
            responder_callback(base_url, callback_id, "Sessão já não existe mais.", show_alert=True)
            return

    print(f"[sessão encerrada] id={session_id} pid={pid}")
    responder_callback(base_url, callback_id, "Sessão encerrada.")
    enviar_mensagem(base_url, chat_id, f"🛑 Sessão {session_id} encerrada.")


def _id_de_sessao_valido(session_id):
    # ids de sessão são curtos e alfanuméricos (hex + '-'); recusa qualquer coisa
    # fora disso pra não montar caminho de job com '/' ou '..'.
    return bool(session_id) and all(c.isalnum() or c == "-" for c in session_id)


def _mensagem_reaberta(novo_id, url):
    msg = f"🔓 Sessão reaberta em {HOME_DIR}"
    if novo_id:
        msg += f" (novo id {novo_id})"
    return msg


def handle_reabrir_callback(base_url, callback_query):
    callback_id = callback_query["id"]
    chat_id = callback_query["message"]["chat"]["id"]

    if not is_owner(chat_id):
        responder_callback(base_url, callback_id, "🔒 Não autorizado.", show_alert=True)
        return

    dados = callback_query.get("data", "")
    _, _, session_id = dados.partition(":")
    session_id = session_id.strip()
    if not _id_de_sessao_valido(session_id):
        responder_callback(base_url, callback_id, "Sessão inválida.", show_alert=True)
        return

    responder_callback(base_url, callback_id, "Reabrindo…")
    ok, novo_id, url, erro = reabrir_sessao(session_id)
    if not ok:
        enviar_mensagem(base_url, chat_id, f"Não consegui reabrir a sessão: {erro}")
        return
    enviar_mensagem(base_url, chat_id, _mensagem_reaberta(novo_id, url))


def handle_reabrir(base_url, chat_id, session_id):
    if not is_owner(chat_id):
        enviar_mensagem(base_url, chat_id, "🔒 Não autorizado.")
        return

    session_id = session_id.strip()
    if not session_id:
        enviar_mensagem(
            base_url, chat_id,
            "Uso: /reabrir <id da sessão>. Veja os ids no /status "
            "(as sessões bloqueadas têm o botão 🔓 Reabrir).",
        )
        return
    if not _id_de_sessao_valido(session_id):
        enviar_mensagem(base_url, chat_id, "Sessão inválida.")
        return

    ok, novo_id, url, erro = reabrir_sessao(session_id)
    if not ok:
        enviar_mensagem(base_url, chat_id, f"Não consegui reabrir a sessão: {erro}")
        return
    enviar_mensagem(base_url, chat_id, _mensagem_reaberta(novo_id, url))


def process_update(update, base_url):
    if "callback_query" in update:
        dados = update["callback_query"].get("data", "")
        if dados.startswith("reabrir:"):
            handle_reabrir_callback(base_url, update["callback_query"])
        else:
            handle_fechar_callback(base_url, update["callback_query"])
        return

    message = update.get("message")
    if not message or "text" not in message:
        return

    chat_id = message["chat"]["id"]
    texto = message["text"].strip()
    print(f"[msg {chat_id}] {texto}")

    if texto == "/start":
        handle_start(base_url, chat_id)
        return

    if texto == "/claude" or texto.startswith("/claude "):
        prompt = texto[len("/claude"):].strip()
        handle_claude(base_url, chat_id, prompt)
        return

    if texto == "/status":
        handle_status(base_url, chat_id)
        return

    if texto == "/reabrir" or texto.startswith("/reabrir "):
        arg = texto[len("/reabrir"):].strip()
        handle_reabrir(base_url, chat_id, arg)
        return

    if texto == "/pastas":
        handle_pastas(base_url, chat_id)
        return

    if OWNER_CHAT_ID is None:
        # bot ainda sem dono travado - so /start e processado (evita dar
        # qualquer resposta que confirme a existencia/funcao do bot a estranhos)
        return

    if is_owner(chat_id):
        enviar_mensagem(
            base_url, chat_id,
            "Comandos disponíveis:\n/claude [instrução opcional]\n/status\n"
            "/reabrir <id>\n/pastas",
        )
    else:
        enviar_mensagem(base_url, chat_id, "🔒 Não autorizado.")


def main():
    global OWNER_CHAT_ID

    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN não encontrado em .env")
    OWNER_CHAT_ID = env.get("OWNER_CHAT_ID") or None

    base_url = f"https://api.telegram.org/bot{token}"
    offset = load_offset()

    print("Bot telegram-claude-remote iniciado. Aguardando mensagens no Telegram...")
    if OWNER_CHAT_ID:
        print(f"Acesso já travado no chat_id {OWNER_CHAT_ID}.")
    else:
        print("Sem dono travado ainda — aguardando o primeiro /start.")

    while True:
        params = {"timeout": POLL_TIMEOUT}
        if offset is not None:
            params["offset"] = offset

        try:
            resp = requests.get(f"{base_url}/getUpdates", params=params, timeout=POLL_TIMEOUT + 10)
            resp.raise_for_status()
            updates = resp.json().get("result", [])
        except requests.RequestException as e:
            print(f"[erro telegram] {e}")
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            save_offset(offset)
            try:
                process_update(update, base_url)
            except Exception as e:
                print(f"[erro ao processar update] {e}")


if __name__ == "__main__":
    main()
