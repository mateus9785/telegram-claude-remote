"""Paths, timeouts e persistência em arquivo (.env / offset) do bot."""

import os

# config.py mora em telegram_claude_remote/, uma subpasta do repo — SCRIPT_DIR
# precisa subir DOIS níveis a partir de __file__ pra chegar na raiz do repo, onde
# .env/.telegram_offset de verdade ficam. Não é engano, é o preço de mover o código
# pra dentro do pacote.
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
OFFSET_PATH = os.path.join(SCRIPT_DIR, ".telegram_offset")

HOME_DIR = "/home/ubuntu"
JOBS_DIR = os.path.join(HOME_DIR, ".claude", "jobs")

POLL_TIMEOUT = 30
CLAUDE_LAUNCH_TIMEOUT = 60
CLAUDE_STATUS_TIMEOUT = 20
CLAUDE_REOPEN_TIMEOUT = 60


def load_env() -> dict[str, str]:
    valores: dict[str, str] = {}
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            chave, _, valor = line.partition("=")
            valores[chave.strip()] = valor.strip()
    return valores


def set_owner_chat_id(chat_id: str) -> None:
    """Reescreve a linha OWNER_CHAT_ID=... no .env, preservando o resto do arquivo."""
    with open(ENV_PATH, encoding="utf-8") as f:
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


def load_offset() -> int | None:
    if os.path.exists(OFFSET_PATH):
        with open(OFFSET_PATH, encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                return int(content)
    return None


def save_offset(offset: int) -> None:
    with open(OFFSET_PATH, "w", encoding="utf-8") as f:
        f.write(str(offset))


class BotState:
    """Substitui o antigo global mutável OWNER_CHAT_ID. Uma instância única é
    criada em main() e passada explicitamente pelos handlers em vez de lida/mutada
    via `global`."""

    def __init__(self, owner_chat_id: str | None = None) -> None:
        self.owner_chat_id = owner_chat_id

    def is_owner(self, chat_id: int | str) -> bool:
        return self.owner_chat_id is not None and str(chat_id) == str(self.owner_chat_id)

    def lock_to(self, chat_id: int | str) -> None:
        self.owner_chat_id = str(chat_id)
        set_owner_chat_id(self.owner_chat_id)
