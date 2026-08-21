"""Controle do Claude Code via subprocess: lançar/listar/encerrar/reabrir sessões."""

import json
import logging
import os
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime

from . import config

logger = logging.getLogger(__name__)

# tira os códigos de cor ANSI da saída do `claude --bg` pra achar o id novo
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _id_de_sessao_valido(session_id):
    # ids de sessão são curtos e alfanuméricos (hex + '-'); recusa qualquer coisa
    # fora disso pra não montar caminho de job com '/' ou '..'.
    return bool(session_id) and all(c.isalnum() or c == "-" for c in session_id)


def launch_session(prompt):
    """Lança `claude --remote-control ... --bg`. Retorna (ok, session_name, erro):
    ok=True -> session_name preenchido, erro None.
    ok=False -> session_name None, erro com a mensagem pronta pro usuário."""
    session_name = f"tg-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    cmd = ["claude", "--remote-control", session_name, "--bg", "--dangerously-skip-permissions"]
    if prompt:
        cmd.append(prompt)

    logger.info("lançando %s (cwd=%s)", cmd, config.HOME_DIR)
    try:
        result = subprocess.run(
            cmd, cwd=config.HOME_DIR, capture_output=True, text=True, timeout=config.CLAUDE_LAUNCH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, None, "Tempo esgotado tentando abrir a sessão. Tente /status para conferir."

    if result.returncode != 0:
        erro = (result.stderr or result.stdout).strip()
        logger.error("erro claude --remote-control: %s", erro)
        return False, None, f"Falha ao abrir a sessão:\n{erro[:1500]}"

    return True, session_name, None


def list_sessions():
    """Roda `claude agents --json`. Retorna (ok, sessoes, erro):
    ok=True -> sessoes é a lista já parseada do JSON, erro None.
    ok=False -> sessoes None, erro com a mensagem pronta pro usuário."""
    try:
        result = subprocess.run(
            ["claude", "agents", "--json"],
            cwd=config.HOME_DIR, capture_output=True, text=True, timeout=config.CLAUDE_STATUS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, None, "Tempo esgotado consultando as sessões."

    if result.returncode != 0:
        return False, None, f"Erro ao consultar sessões:\n{result.stderr.strip()[:1500]}"

    try:
        sessoes = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, None, "Não consegui interpretar a resposta do `claude agents --json`."

    return True, sessoes, None


def buscar_sessao_por_id(session_id):
    """Consulta `claude agents --json` e devolve o dict da sessão com esse id, ou None."""
    try:
        result = subprocess.run(
            ["claude", "agents", "--json"],
            cwd=config.HOME_DIR, capture_output=True, text=True, timeout=config.CLAUDE_STATUS_TIMEOUT,
        )
        sessoes = json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return None
    for s in sessoes:
        if str(s.get("id")) == str(session_id):
            return s
    return None


def encerrar_sessao(session_id):
    """Encerra (SIGTERM) o processo da sessão, ou limpa o job dir se for fantasma
    (sem pid). Retorna (ok, erro): ok=True -> encerrada com sucesso, erro None.
    ok=False -> erro com a mensagem pronta pro usuário (alerta de callback)."""
    sessao = buscar_sessao_por_id(session_id)
    pid = sessao.get("pid") if sessao else None

    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pid = None  # processo já saiu — cai na limpeza do estado abaixo
        except (PermissionError, ValueError):
            return False, "Sem permissão para encerrar essa sessão."

    if not pid:
        # sessão-fantasma (bloqueada, sem processo): remove o estado do job pra
        # tirá-la da lista do `claude agents`.
        job_dir = os.path.join(config.JOBS_DIR, session_id)
        if os.path.isdir(job_dir):
            try:
                shutil.rmtree(job_dir)
            except OSError as e:
                return False, f"Falha ao limpar a sessão: {e}"
        else:
            return False, "Sessão já não existe mais."

    logger.info("sessão encerrada id=%s pid=%s", session_id, pid)
    return True, None


def carregar_state(session_id):
    """Lê o state.json do job dessa sessão (guarda resumeSessionId, respawnFlags, bridge)."""
    caminho = os.path.join(config.JOBS_DIR, session_id, "state.json")
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


def extrair_novo_id(saida):
    """Acha o id novo de sessão na saída do `claude --resume ... --bg`
    (formato 'backgrounded · <id> (idle — ...)'), já sem códigos ANSI.
    None se não achar."""
    limpo = ANSI_RE.sub("", saida)
    m = re.search(r"backgrounded\s*·?\s*([0-9a-f]{6,})", limpo)
    return m.group(1) if m else None


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

    logger.info("reabrindo %s (cwd=%s)", cmd, config.HOME_DIR)
    try:
        result = subprocess.run(
            cmd, cwd=config.HOME_DIR, capture_output=True, text=True, timeout=config.CLAUDE_REOPEN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, None, None, "tempo esgotado ao reabrir. Confira com /status."

    if result.returncode != 0:
        return False, None, None, (result.stderr or result.stdout).strip()[:1500]

    novo_id = extrair_novo_id(result.stdout)

    # remove o fantasma antigo pra não aparecer duplicado no /status
    if novo_id and novo_id != session_id:
        antigo = os.path.join(config.JOBS_DIR, session_id)
        if os.path.isdir(antigo):
            try:
                shutil.rmtree(antigo)
            except OSError:
                logger.warning("não limpei o fantasma %s", session_id, exc_info=True)

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
