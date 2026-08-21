"""Handlers de comando/callback do Telegram e o dispatch de updates."""

import logging
import os
from datetime import datetime

from . import claude_control, config
from .telegram_client import enviar_mensagem, responder_callback

logger = logging.getLogger(__name__)

_COMANDOS_COM_ARGUMENTO = {"/claude", "/reabrir"}
_COMANDOS = ("/start", "/claude", "/status", "/reabrir", "/pastas")


def parse_command(texto):
    """Separa o texto de uma mensagem em (comando, argumento). `comando` inclui a
    barra ('/claude', '/status', ...) ou é None se o texto não for nenhum comando
    reconhecido; `argumento` é '' quando não há argumento (ou o comando não aceita)."""
    for comando in _COMANDOS:
        if texto == comando:
            return comando, ""
        if comando in _COMANDOS_COM_ARGUMENTO and texto.startswith(comando + " "):
            return comando, texto[len(comando):].strip()
    return None, ""


def handle_start(base_url, chat_id, state):
    if state.owner_chat_id is None:
        state.lock_to(chat_id)
        logger.info("acesso travado OWNER_CHAT_ID=%s", state.owner_chat_id)
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

    if state.is_owner(chat_id):
        enviar_mensagem(base_url, chat_id, "Você já é o dono autorizado deste bot.")
    else:
        enviar_mensagem(base_url, chat_id, "🔒 Não autorizado.")


def handle_claude(base_url, chat_id, prompt, state):
    if not state.is_owner(chat_id):
        enviar_mensagem(base_url, chat_id, "🔒 Não autorizado.")
        return

    ok, session_name, erro = claude_control.launch_session(prompt)
    if not ok:
        enviar_mensagem(base_url, chat_id, erro)
        return

    enviar_mensagem(base_url, chat_id, f"✅ Sessão \"{session_name}\" aberta em {config.HOME_DIR}")


def handle_status(base_url, chat_id, state):
    if not state.is_owner(chat_id):
        enviar_mensagem(base_url, chat_id, "🔒 Não autorizado.")
        return

    ok, sessoes, erro = claude_control.list_sessions()
    if not ok:
        enviar_mensagem(base_url, chat_id, erro)
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


def handle_pastas(base_url, chat_id, state):
    if not state.is_owner(chat_id):
        enviar_mensagem(base_url, chat_id, "🔒 Não autorizado.")
        return

    try:
        pastas = sorted(
            entry.name for entry in os.scandir(config.HOME_DIR)
            if entry.is_dir() and not entry.name.startswith(".")
        )
    except OSError as e:
        enviar_mensagem(base_url, chat_id, f"Erro ao listar pastas: {e}")
        return

    if not pastas:
        enviar_mensagem(base_url, chat_id, f"Nenhuma pasta encontrada em {config.HOME_DIR}.")
        return

    texto = f"Pastas em {config.HOME_DIR}:\n" + "\n".join(f"• {p}" for p in pastas)
    enviar_mensagem(base_url, chat_id, texto)


def _mensagem_reaberta(novo_id, url):
    msg = f"🔓 Sessão reaberta em {config.HOME_DIR}"
    if novo_id:
        msg += f" (novo id {novo_id})"
    return msg


def handle_fechar_callback(base_url, callback_query, state):
    callback_id = callback_query["id"]
    chat_id = callback_query["message"]["chat"]["id"]

    if not state.is_owner(chat_id):
        responder_callback(base_url, callback_id, "🔒 Não autorizado.", show_alert=True)
        return

    dados = callback_query.get("data", "")
    _, _, session_id = dados.partition(":")
    session_id = session_id.strip()
    if not claude_control._id_de_sessao_valido(session_id):
        responder_callback(base_url, callback_id, "Sessão inválida.", show_alert=True)
        return

    ok, erro = claude_control.encerrar_sessao(session_id)
    if not ok:
        responder_callback(base_url, callback_id, erro, show_alert=True)
        return

    responder_callback(base_url, callback_id, "Sessão encerrada.")
    enviar_mensagem(base_url, chat_id, f"🛑 Sessão {session_id} encerrada.")


def handle_reabrir_callback(base_url, callback_query, state):
    callback_id = callback_query["id"]
    chat_id = callback_query["message"]["chat"]["id"]

    if not state.is_owner(chat_id):
        responder_callback(base_url, callback_id, "🔒 Não autorizado.", show_alert=True)
        return

    dados = callback_query.get("data", "")
    _, _, session_id = dados.partition(":")
    session_id = session_id.strip()
    if not claude_control._id_de_sessao_valido(session_id):
        responder_callback(base_url, callback_id, "Sessão inválida.", show_alert=True)
        return

    responder_callback(base_url, callback_id, "Reabrindo…")
    ok, novo_id, url, erro = claude_control.reabrir_sessao(session_id)
    if not ok:
        enviar_mensagem(base_url, chat_id, f"Não consegui reabrir a sessão: {erro}")
        return
    enviar_mensagem(base_url, chat_id, _mensagem_reaberta(novo_id, url))


def handle_reabrir(base_url, chat_id, session_id, state):
    if not state.is_owner(chat_id):
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
    if not claude_control._id_de_sessao_valido(session_id):
        enviar_mensagem(base_url, chat_id, "Sessão inválida.")
        return

    ok, novo_id, url, erro = claude_control.reabrir_sessao(session_id)
    if not ok:
        enviar_mensagem(base_url, chat_id, f"Não consegui reabrir a sessão: {erro}")
        return
    enviar_mensagem(base_url, chat_id, _mensagem_reaberta(novo_id, url))


def process_update(update, base_url, state):
    if "callback_query" in update:
        dados = update["callback_query"].get("data", "")
        if dados.startswith("reabrir:"):
            handle_reabrir_callback(base_url, update["callback_query"], state)
        else:
            handle_fechar_callback(base_url, update["callback_query"], state)
        return

    message = update.get("message")
    if not message or "text" not in message:
        return

    chat_id = message["chat"]["id"]
    texto = message["text"].strip()
    logger.info("msg %s: %s", chat_id, texto)

    comando, arg = parse_command(texto)

    if comando == "/start":
        handle_start(base_url, chat_id, state)
        return

    if comando == "/claude":
        handle_claude(base_url, chat_id, arg, state)
        return

    if comando == "/status":
        handle_status(base_url, chat_id, state)
        return

    if comando == "/reabrir":
        handle_reabrir(base_url, chat_id, arg, state)
        return

    if comando == "/pastas":
        handle_pastas(base_url, chat_id, state)
        return

    if state.owner_chat_id is None:
        # bot ainda sem dono travado - so /start e processado (evita dar
        # qualquer resposta que confirme a existencia/funcao do bot a estranhos)
        return

    if state.is_owner(chat_id):
        enviar_mensagem(
            base_url, chat_id,
            "Comandos disponíveis:\n/claude [instrução opcional]\n/status\n"
            "/reabrir <id>\n/pastas",
        )
    else:
        enviar_mensagem(base_url, chat_id, "🔒 Não autorizado.")
